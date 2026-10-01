"""
geo_agent.py - وكيل المخاطر الجغرافية والأثر (Geospatial Risk & Impact Agent)

الفرق عن الثلاثة وكلاء المختصين (Meter/Plant/Charging): هذا الوكيل مش يشخّص
سبب الحادثة (ما يدخل بمنافسة الفرضيات ولا بجولة النقاش)، إنما يشتغل بعد ما
يحسم الـ Arbitrator (resolved أو undecided) ويحلل الأثر الجغرافي والمالي
والتشغيلي للقرار على منطقة الحادثة ومحيطها. كل رقم يطلعه محسوب بالكود من
بيانات حقيقية (tools/geo_tools.py)، بدون أي نداء Groq — نفس مبدأ
services/confidence.py وservices/approvals.py بالضبط: الثقة والقرار المالي
ما ينبغي يُترك لتخمين نموذج لغوي.

الاستخدام:
    from agents.geo_agent import run_geo_assessment
    assessment = run_geo_assessment(incident_id, emit=emit)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import geo_tools as gt  # noqa: E402
from db import database as db      # noqa: E402


def _evidence_magnitude(winner_agent, evidence):
    """
    نفس فكرة evidenceMagnitude() بالواجهة (JS): نستخرج مقدار الشذوذ الفعلي
    من الأدلة الخام نفسها بدل ما نعتمد بس على الثقة. evidence هون قائمة
    dicts من results[winner]["evidence"] (شكل tool/input/output حسب العقد).
    """
    if not evidence:
        return None
    if winner_agent == "Meter Agent":
        for ev in evidence:
            out = ev.get("output", {}) if isinstance(ev, dict) else {}
            if isinstance(out.get("drop_pct"), (int, float)):
                return min(1.3, abs(out["drop_pct"]) / 60)
            own = out.get("own_avg_kwh")
            neigh = out.get("neighbors_avg_kwh")
            if isinstance(own, (int, float)) and isinstance(neigh, (int, float)) and neigh:
                pct = abs(neigh - own) / neigh
                return min(1.3, pct * 1.6)
        return None
    best = 0.0
    for ev in evidence:
        out = ev.get("output", {}) if isinstance(ev, dict) else {}
        s = out.get("strength")
        if isinstance(s, (int, float)) and s > best:
            best = s
    return min(1.3, best * 1.3) if best else None


def run_geo_assessment(incident_id, emit=print):
    """
    يبني التقييم الجغرافي الكامل لحادثة انقفلت (resolved أو undecided)،
    يبثّه عبر emit (يغذّي 4 الـ Charts بالواجهة)، ويحفظه بقاعدة البيانات.
    آمن يستدعى حتى لو الحادثة undecided (يرجع أثر مالي None، بس يحسب
    ضغط المنطقة/الخطر الجغرافي/زمن الاستجابة عادي — كلها مستقلة عن الفوز).
    """
    incident = db.get_incident(incident_id)
    if not incident:
        emit({"type": "geo_error", "incident_id": incident_id, "error": "incident not found"})
        return None

    region = incident.get("region")
    winner = incident.get("winner_agent")
    confidence = incident.get("final_confidence")

    stress = gt.get_regional_load_stress(region) if region else None
    cascading = gt.estimate_cascading_risk(region) if region else None

    burn_rate = None
    if winner and confidence is not None:
        burn_rate = gt.compute_financial_burn_rate(winner, confidence)

    response = gt.compute_response_time_index(incident_id)
    if response and response.get("error"):
        response = None

    assessment = {
        "incident_id": incident_id,
        "region": region,
        "stress": stress,
        "cascading": cascading,
        "burn_rate": burn_rate,
        "response": response,
    }

    emit({"type": "geo_assessment", **assessment})

    db.save_geo_assessment(
        incident_id, region=region, stress=stress, cascading=cascading,
        burn_rate=burn_rate, response=response,
    )
    return assessment


if __name__ == "__main__":
    # اختبار سريع بدون Groq: python agents/geo_agent.py <incident_id>
    iid = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    import json
    out = run_geo_assessment(iid, emit=lambda e: None)
    print(json.dumps(out, ensure_ascii=False, indent=2))
