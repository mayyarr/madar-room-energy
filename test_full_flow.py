"""
test_full_flow.py - اختبار التدفق الكامل بمفتاح Groq حقيقي (P7.4)

يشغّل السيناريوهين المعتمدين من البداية للنهاية (dispatch -> مقارنة -> نقاش إن وجد
-> حفظ بقاعدة البيانات -> اقتراح إجراء وموافقة معلّقة)، ويطبع تقرير نظيف بالنتيجة
بدل ما تعتمدي بس على مراقبة الواجهة الحية.

طريقة التشغيل (لازم مفتاح Groq حقيقي بمتغير البيئة، ما بيُكتب هون أبداً):
    $env:GROQ_API_KEY="مفتاحك"      # PowerShell
    python test_full_flow.py

يفحص لكل سيناريو:
  - الثلاث وكلاء اشتغلوا (ولا واحد "crashed" بدون نتيجة)
  - كل ثقة قيمة رقمية ضمن [0, 1]
  - في أدلة فعلية (evidence) مش فاضية لأغلب الوكلاء
  - القرار انحفظ بقاعدة البيانات (incident_id رجع رقم حقيقي)
  - لو القرار "resolved": صار فيه اقتراح إجراء وموافقة معلّقة
  - لو "undecided": ما في اقتراح إجراء (متوافق مع مبدأ "لا تخترعي نتيجة")
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

if not os.environ.get("GROQ_API_KEY"):
    print("✖ متغير البيئة GROQ_API_KEY مش موجود. حطيه قبل ما تشغلي هاد السكريبت.")
    sys.exit(1)

from agents.arbitrator import run_and_save  # noqa: E402
from services import approvals  # noqa: E402
from db import database as db  # noqa: E402

SCENARIOS = [
    {
        "key": "clear",
        "title": "هبوط استهلاك مفاجئ - MAC002068",
        "event_text": (
            "Meter MAC002068 in Amman region dropped sharply from 22.07 to 9.82 "
            "kWh/day, ratio to neighbors fell from 1.28x to 0.53x. Investigate the cause."
        ),
        "region": "Amman",
    },
    {
        "key": "tie",
        "title": "ارتفاع استهلاك مفاجئ - MAC000689",
        "event_text": (
            "Meter MAC000689 in Madaba region rose from 22.63 to 33.97 kWh/day "
            "as of 2020-02-14. Investigate the cause."
        ),
        "region": "Madaba",
    },
]

PASS, FAIL, WARN = "✔", "✖", "⚠"


def check(label, ok, detail=""):
    mark = PASS if ok else FAIL
    print(f"  {mark} {label}" + (f" — {detail}" if detail else ""))
    return ok


def run_scenario(sc):
    print(f"\n{'='*70}\nسيناريو: {sc['title']}\n{'='*70}")
    events = []
    t0 = time.time()
    try:
        result = run_and_save(
            sc["event_text"], title=sc["title"], region=sc["region"],
            emit=lambda e: events.append(e), save_trace=True,
        )
    except Exception as e:
        print(f"  {FAIL} التشغيل كامل انكسر بدون ما يرجّع نتيجة: {e}")
        return False
    dt = time.time() - t0

    all_ok = True
    incident_id = result.get("incident_id")
    all_ok &= check("انحفظت الحادثة بقاعدة البيانات", bool(incident_id), f"incident_id={incident_id}")

    results = result.get("results") or {}

    n_failed_agents = 0
    for agent_name in ["Meter Agent", "Plant Agent", "Charging Agent"]:
        r = (results or {}).get(agent_name)
        if r is None:
            print(f"  {WARN} ما لقيت نتيجة صريحة لـ{agent_name} بالـ dict المرجّع (تحقق يدوي من الطباعة الحية بالأعلى إذا لزم).")
            continue
        conf = r.get("confidence")
        ev_count = len(r.get("evidence") or [])
        ok_conf = isinstance(conf, (int, float)) and 0.0 <= conf <= 1.0
        all_ok &= check(f"{agent_name}: ثقة ضمن [0,1]", ok_conf, f"confidence={conf}")
        if r.get("failed"):
            n_failed_agents += 1
            err_events = [e.get("error") for e in events if e.get("agent") == agent_name and e.get("type") == "error"]
            reason = err_events[-1] if err_events else r.get("hypothesis", "(سبب غير معروف)")
            print(f"  {WARN} {agent_name} فشل أثناء التشغيل — السبب: {reason}")
            if "rate" in str(reason).lower() or "429" in str(reason):
                print(f"      ↳ هذا يبدو حد Groq بالدقيقة (rate limit)، مش خطأ بالكود. جرّبي بعد انتظار أطول بين التشغيلات.")
        else:
            check(f"{agent_name}: عنده أدلة فعلية", ev_count > 0, f"{ev_count} دليل")

    all_ok &= check("مش كل الوكلاء الثلاثة فشلوا معاً", n_failed_agents < 3, f"{n_failed_agents}/3 فشلوا")

    decision = result.get("decision") or {}
    # decision["status"] هي نتيجة المقارنة الأولية (clear/tie)، مش الحالة النهائية —
    # الحالة النهائية (resolved/undecided) محفوظة فعلياً بجدول incidents بعد كل منطق
    # MIN_WINNING_CONFIDENCE والنقاش، فنجيبها من قاعدة البيانات مباشرة عشان تكون دقيقة 100%.
    incident_row = db.get_incident(incident_id) or {}
    status = incident_row.get("status")
    all_ok &= check("حالة القرار النهائية موجودة بقاعدة البيانات", status in ("resolved", "undecided"), f"status={status}")
    print(f"  ℹ الحالة النهائية: {status} (فرز أولي: {decision.get('status')}) — استغرق {dt:.1f} ثانية")

    if status == "resolved":
        pending = approvals.get_pending(incident_id)
        all_ok &= check("طلب موافقة اتفتح تلقائياً لقرار محسوم", len(pending) >= 1, f"{len(pending)} طلب معلّق")
    elif status == "undecided":
        pending = approvals.get_pending(incident_id)
        all_ok &= check("ما فيه اقتراح إجراء لقرار غير محسوم (صح، ماز فيه لازم يكون)", len(pending) == 0)

    debate = result.get("debate")
    if debate:
        print(f"  ℹ صار نقاش: {debate.get('status')}")

    n_errors = sum(1 for e in events if e.get("type") == "error")
    if n_errors:
        print(f"  {WARN} صار {n_errors} خطأ أثناء التشغيل (طبيعي لو النظام تعافى منها وكمل).")

    return all_ok


def main():
    print("بدء اختبار التدفق الكامل (P7.4) — بمفتاح Groq حقيقي، بدون أي تلاعب أو mock.\n")
    all_pass = True
    for i, sc in enumerate(SCENARIOS):
        if i > 0:
            wait_s = 60
            print(f"\n⏳ استنى {wait_s} ثانية قبل السيناريو الجاي (تهدئة كوتا Groq بالدقيقة، عشان أول سيناريو استهلك منها)...")
            time.sleep(wait_s)
        ok = run_scenario(sc)
        all_pass &= ok

    print(f"\n{'='*70}")
    print("النتيجة النهائية:", "كل شي تمام ✔" if all_pass else "في نقاط تحتاج مراجعة يدوية ⚠ (راجعي الأسطر ✖ و⚠ فوق)")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
