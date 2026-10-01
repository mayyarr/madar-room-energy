"""
synthetic_agent.py - وكيل الحوادث الاصطناعية (Synthetic Incident Agent)

دوره مش إنه يشخّص — دوره يمتحن النظام. بيشتغل على داتا أردنية اصطناعية
(data/jordan_synthetic/) انحقن فيها أعطال بنعرف سببها الحقيقي من الأصل،
وبيقارن جواب الوكلاء الثلاثة مع مفتاح الإجابات (scenarios.json).

خطوتين لكل حادثة:
1. verify(id)  — بدون Groq: بيتأكد إنه العطل المحقون موجود فعلاً بالداتا
   (نفس أدوات الوكلاء، بمدخلات ثابتة). لو فشلت هون، المشكلة بالداتا مش بالوكلاء.
2. run(id)     — بيفتح الحادثة ويشغّل التحقيق الكامل الحقيقي (run_and_save: الوكلاء
   + التحدي + التحكيم)، وبعدين بيصحّح النتيجة: الحالة (محسوم/غير محسوم) والفائز
   لازم يطابقوا المتوقع.

الوكيل ما بيلمس منطق الثقة ولا التحكيم — بس بيراقب النتيجة وبيحكم عليها.
"""
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import meter_tools as mt, charging_tools as ct, plant_tools as pt  # noqa: E402
from db import database as db  # noqa: E402

SCENARIOS_FILE = os.path.join(ROOT, "data", "jordan_synthetic", "scenarios.json")
AGENT_NAME = "Synthetic Incident Agent"

_TOOLS = {
    **{n: getattr(mt, n) for n in ("detect_consumption_drop", "compare_neighbor_meters",
                                   "compare_history_vs_neighbors", "check_data_quality")},
    **{n: getattr(ct, n) for n in ("detect_charging_load_shift", "detect_new_charger_activity",
                                   "compare_charger_failure_rate", "explain_meter_change",
                                   "check_charging_data_quality")},
    **{n: getattr(pt, n) for n in ("detect_thermal_anomaly", "get_degradation_trend",
                                   "check_sensor_quality", "estimate_remaining_life")},
}

_lock = threading.Lock()
_busy = {"on": False}


def load():
    if not os.path.exists(SCENARIOS_FILE):
        return []
    with open(SCENARIOS_FILE, encoding="utf-8") as f:
        return json.load(f)


def get(scenario_id):
    return next((s for s in load() if s["id"] == scenario_id), None)


def public_list():
    """القائمة للواجهة — بدون مفتاح الإجابة (عشان ما ينكشف قبل التشغيل)."""
    return [{"id": s["id"], "title": s["title"], "domain": s["domain"], "region": s["region"]}
            for s in load()]


def verify(scenario_id):
    """فحص الداتا بدون Groq: هل العطل المحقون فعلاً موجود وبتلقطه الأدوات؟"""
    s = get(scenario_id)
    if not s:
        return {"error": "unknown scenario"}
    out = []
    for c in s["checks"]:
        r = _TOOLS[c["tool"]](**c["args"])
        ok = bool(r.get("flagged")) == c["flagged"] and "error" not in r
        if "field" in c:
            ok = ok and r.get(c["field"]) == c["value"]
        out.append({"tool": c["tool"], "ok": ok, "flagged": r.get("flagged"),
                    "summary": r.get("summary") or r.get("error")})
    return {"id": scenario_id, "data_ok": all(x["ok"] for x in out), "checks": out}


def grade(scenario, incident, hypotheses, failed_agents=()):
    """يقارن نتيجة التحقيق الفعلية مع المتوقع."""
    status_ok = incident.get("status") == scenario["expected_status"]
    winner_ok = incident.get("winner_agent") == scenario["expected_winner"]
    # الكلمات المفتاحية معلومة إضافية بس (نص الموديل بيتغير)، مش شرط للنجاح
    focus = scenario["expected_winner"] or {"meter": "Meter Agent", "charging": "Charging Agent",
                                            "plant": "Plant Agent"}[scenario["domain"]]
    text = " ".join(h["hypothesis"] for h in hypotheses if h["agent"] == focus).lower()
    kw_hit = [k for k in scenario.get("expected_keywords", []) if k.lower() in text]
    verdict = "PASS" if (status_ok and winner_ok) else "FAIL"
    if failed_agents:
        verdict = "INVALID"   # وكيل أو أكثر ما اشتغل (غالباً حد Groq) — النتيجة مش امتحان حقيقي
    return {
        "scenario_id": scenario["id"],
        "verdict": verdict, "failed_agents": list(failed_agents),
        "status_expected": scenario["expected_status"], "status_got": incident.get("status"),
        "winner_expected": scenario["expected_winner"], "winner_got": incident.get("winner_agent"),
        "final_confidence": incident.get("final_confidence"),
        "keywords_found": kw_hit,
        "injected_fault": scenario["injected_fault"],
    }


def _hypotheses(incident_id):
    conn = db.get_conn()
    rows = conn.execute("SELECT agent, hypothesis, confidence, status FROM hypotheses "
                        "WHERE incident_id = ? ORDER BY id", (incident_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _run(scenario, publish):
    from agents.arbitrator import run_and_save
    try:
        data = verify(scenario["id"])
        publish({"type": "synthetic_started", "scenario_id": scenario["id"], "title": scenario["title"],
                 "data_ok": data["data_ok"]})
        publish({"type": "incident_started", "title": "🧪 " + scenario["title"]})
        result = run_and_save(scenario["event_text"], title="🧪 " + scenario["title"],
                              region=scenario["region"], emit=publish, save_trace=True,
                              force_three_way=True)
        iid = result["incident_id"]
        publish({"type": "incident_saved", "incident_id": iid,
                 "decision": result["decision"]["status"],
                 "debate": result["debate"]["status"] if result["debate"] else None})
        failed = [n for n, r in result["results"].items() if r.get("failed")]
        g = grade(scenario, db.get_incident(iid), _hypotheses(iid), failed)
        g["incident_id"] = iid
        g["data_ok"] = data["data_ok"]
        db.insert_trace_event(iid, AGENT_NAME, "synthetic_grade", g)
        publish({"type": "synthetic_grade", **g})
    except Exception as e:  # noqa: BLE001
        publish({"type": "synthetic_error", "error": str(e)})
    finally:
        with _lock:
            _busy["on"] = False


def start(scenario_id, publish):
    s = get(scenario_id)
    if not s:
        return "unknown"
    with _lock:
        if _busy["on"]:
            return "busy"
        _busy["on"] = True
    threading.Thread(target=_run, args=(s, publish), daemon=True).start()
    return "started"


if __name__ == "__main__":
    # فحص الداتا لكل الحوادث بدون Groq:  python agents/synthetic_agent.py
    for sc in load():
        v = verify(sc["id"])
        print(("✔" if v["data_ok"] else "✖"), sc["id"], sc["title"])
        for c in v["checks"]:
            print("    ", "✔" if c["ok"] else "✖", c["tool"], "-", (c["summary"] or "")[:90])
