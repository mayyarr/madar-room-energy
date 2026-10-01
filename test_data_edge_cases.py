"""
test_data_edge_cases.py - اختبار البيانات الناقصة أو المتضاربة (P7.1)

يفحص أدوات الوكلاء الثلاثة مباشرة (بدون Groq، بدون agent.run) بمدخلات:
  - معرّف/منطقة غير موجودة إطلاقاً
  - بيانات فارغة تماماً
  - تاريخ (as_of_day) خارج مدى البيانات الفعلي (مستقبل/قبل البداية)
  - نافذة (window) أكبر من عدد الأيام المتوفرة فعلياً
  - قيم حدّية (window=0 أو سالبة)

الهدف: التأكد أن كل أداة ترجع dict فيه "error" أو استجابة منطقية (مش استثناء غير
ملتقط يوقف التشغيل بالكامل ويكسر agent.run/dispatch وقت الديمو).

ما يحتاج GROQ_API_KEY ولا إنترنت. يشتغل مباشرة على madar.db الموجودة.
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from tools import meter_tools as mt   # noqa: E402
from tools import plant_tools as pt   # noqa: E402
from tools import charging_tools as ct  # noqa: E402

PASS, FAIL, WARN = "✔", "✖", "⚠"
_summary = {"pass": 0, "fail": 0, "warn": 0}


def check(label, fn, *args, **kwargs):
    """
    ينادي fn(*args, **kwargs) ويتأكد:
      - ما طلعت استثناء غير ملتقطة (هذا فشل حقيقي بحد ذاته)
      - النتيجة dict فيها "tool"
      - إذا في "error" أو flagged=False بدون بيانات كافية -> هذا سلوك سليم متوقع (PASS)
      - إذا طلعت قيمة غريبة (مثلاً confidence خارج [0,1] لو موجودة) -> WARN
    """
    try:
        result = fn(*args, **kwargs)
    except Exception as e:
        print(f"  {FAIL} {label}: طلعت استثناء غير ملتقطة — {type(e).__name__}: {e}")
        traceback.print_exc(limit=2)
        _summary["fail"] += 1
        return None

    if not isinstance(result, dict) or "tool" not in result:
        print(f"  {FAIL} {label}: الرجعة مش dict فيها 'tool' — {result!r}")
        _summary["fail"] += 1
        return None

    if "error" in result:
        print(f"  {PASS} {label}: رجعت error نظيف بدل ما تنهار — {result['error']}")
        _summary["pass"] += 1
        return result

    strength = result.get("strength")
    if strength is not None and not (0.0 <= strength <= 1.0):
        print(f"  {WARN} {label}: strength خارج [0,1] — {strength}")
        _summary["warn"] += 1
        return result

    print(f"  {PASS} {label}: رجعت نتيجة سليمة (flagged={result.get('flagged')})")
    _summary["pass"] += 1
    return result


def section(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def main():
    section("1) معرّف/منطقة غير موجودة إطلاقاً")
    check("meter: get_meter_summary(معرّف وهمي)", mt.get_meter_summary, "MAC_GHOST_999")
    check("meter: detect_consumption_drop(معرّف وهمي)", mt.detect_consumption_drop, "MAC_GHOST_999")
    check("meter: check_data_quality(معرّف وهمي)", mt.check_data_quality, "MAC_GHOST_999")
    check("plant: get_unit_summary(معرّف وهمي)", pt.get_unit_summary, 99999)
    check("charging: get_region_charging_summary(منطقة وهمية)", ct.get_region_charging_summary, "Atlantis")
    check("charging: detect_charging_load_shift(منطقة وهمية)", ct.detect_charging_load_shift, "Atlantis")

    section("2) تاريخ (as_of_day) خارج مدى البيانات — مستقبل بعيد")
    check("charging: get_region_charging_summary (سنة 2099)", ct.get_region_charging_summary, "Amman", "2099-01-01")
    check("charging: detect_new_charger_activity (سنة 2099)", ct.detect_new_charger_activity, "Amman", "2099-01-01")

    section("3) تاريخ قبل بداية البيانات فعلياً")
    check("charging: get_region_charging_summary (سنة 1990)", ct.get_region_charging_summary, "Amman", "1990-01-01")

    section("4) نص تاريخ غير صالح (تنسيق خاطئ) — تضارب بالمدخلات")
    check("charging: get_region_charging_summary (تاريخ فاسد)", ct.get_region_charging_summary, "Amman", "مش-تاريخ")

    section("5) قيم نافذة (window) حدّية")
    check("meter: check_data_quality(window=0)", mt.check_data_quality, "MAC002068", 0)
    check("meter: check_data_quality(window=100000)", mt.check_data_quality, "MAC002068", 100000)
    check("meter: detect_consumption_drop(window كبير جداً)", mt.detect_consumption_drop, "MAC002068", 999999)

    section("6) عداد حقيقي بس بمنطقة/سياق متضارب (compare_neighbor مع منطقة مش موجودة أصلاً كمرجع)")
    check("meter: compare_neighbor_meters(معرّف حقيقي)", mt.compare_neighbor_meters, "MAC002068")

    section("7) وحدة معدات برقم دورة (cycle) سالب أو خارج المدى")
    check("plant: detect_thermal_anomaly(as_of_cycle=-5)", pt.detect_thermal_anomaly, 1, -5)
    check("plant: detect_thermal_anomaly(as_of_cycle=999999)", pt.detect_thermal_anomaly, 1, 999999)

    section("النتيجة الإجمالية")
    total = sum(_summary.values())
    print(f"  {PASS} نجح: {_summary['pass']}   {WARN} تحذير: {_summary['warn']}   {FAIL} فشل: {_summary['fail']}   (من أصل {total})")
    if _summary["fail"]:
        print(f"\n  {FAIL} في حالات طلعت استثناء غير ملتقطة — هذه لازم تتصلح قبل الديمو (لو صارت وقت تشغيل حي، الوكيل بينهار بدل ما يرجع 'undecided').")
        sys.exit(1)
    elif _summary["warn"]:
        print(f"\n  {WARN} كل شي ما انهار، بس في قيم غريبة تستاهل مراجعة يدوية.")
    else:
        print(f"\n  {PASS} كل الأدوات تعاملت مع البيانات الناقصة/المتضاربة بشكل نظيف (error بدل انهيار).")


if __name__ == "__main__":
    main()
