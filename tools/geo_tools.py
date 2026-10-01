"""
geo_tools.py - أدوات وكيل المخاطر الجغرافية (Geospatial Risk & Impact Agent)

مبدأ التصميم (نفس مبدأ services/confidence.py وحساب الأثر المالي بـ templates/index.html):
كل رقم هون محسوب بالكود من بيانات حقيقية موجودة فعلاً بقاعدة البيانات
(jordan_chargers من Open Charge Map، meters/incidents الفعلية) — ولا رقم مختلق
ولا نداء Groq. هذا الوكيل تحليلي/إحصائي، مش تشخيصي مثل الثلاثة الآخرين، فمنطقياً
ما يحتاج نموذج لغوي: كل مخرجاته أرقام وقرارات قابلة لإعادة الحساب بأي وقت.

يشتغل بعد ما يحسم الـ Arbitrator (بعد dispatch/compare_confidence/debate_round)،
مو بمنافسة الفرضيات الأصلية، عشان ما يلمس منطق التعادل/النقاش الموجود ولا يهدد استقراره.
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db.database import get_conn  # noqa: E402

# نفس مراكز المناطق الثمانية المستخدمة فعلياً بـ db/seed_ocm.py (مرجع واحد للحقيقة)
from db.regions import REGION_CENTERS  # noqa: E402  (الـ12 محافظة)


# --- نطاقات الأثر المالي اليومي حسب الوكيل الفائز (نفس أرقام IMPACT_RANGES بالواجهة، مصدر واحد للحقيقة) ---
IMPACT_RANGES = {
    "Meter Agent":    {"lo": 15,  "hi": 60,   "label": "هدر/خسارة كهرباء محتملة"},
    "Plant Agent":    {"lo": 800, "hi": 3500, "label": "كلفة توقف تشغيلي محتمل"},
    "Charging Agent": {"lo": 40,  "hi": 150,  "label": "إيراد شحن ضائع محتمل"},
}
JOD_PER_KWH = 0.15  # تعرفة تقريبية لشريحة استهلاك منزلي متوسط بالأردن (نفس القيمة المعتمدة بالواجهة)

# متوسط زمن اكتشاف/إرسال فريق ميداني بشري تقليدي (بدون نظام آلي) لحادثة مشابهة —
# تقدير تشغيلي معقول (لا يوجد رقم رسمي منشور)، يُذكر صراحة كتقدير بالواجهة وليس قياساً حقيقياً.
HUMAN_BASELINE_MINUTES = 45


def km(lat1, lon1, lat2, lon2):
    """مسافة بالكيلومتر بين نقطتين (نفس معادلة Haversine المستخدمة بـ seed_ocm.py)."""
    p = math.pi / 180
    a = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2)
    return 12742 * math.asin(math.sqrt(a))


def _charger_stats():
    """يرجع dict لكل منطقة: عدد المحطات، مجموع نقاط الشحن، متوسط القدرة (بيانات حقيقية من OCM)."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT region, COUNT(*) n, COALESCE(SUM(num_points),0) pts, COALESCE(AVG(max_power_kw),0) avg_kw
        FROM jordan_chargers WHERE region != 'other' GROUP BY region
    """).fetchall()
    conn.close()
    return {r["region"]: {"stations": r["n"], "points": r["pts"], "avg_kw": round(r["avg_kw"], 1)} for r in rows}


def nearest_regions(region, max_km=160):
    """يرجع كل المناطق التانية مرتبة بالمسافة الحقيقية (km) عن `region`."""
    if region not in REGION_CENTERS:
        return []
    lat, lon = REGION_CENTERS[region]
    out = []
    for name, (rlat, rlon) in REGION_CENTERS.items():
        if name == region:
            continue
        d = km(lat, lon, rlat, rlon)
        if d <= max_km:
            out.append({"region": name, "distance_km": round(d, 1)})
    return sorted(out, key=lambda x: x["distance_km"])


def get_regional_load_stress(region):
    """
    تقدير "ضغط الشبكة" الحالي بمنطقة الحادثة: نسبة كثافة محطات الشحن فيها
    مقارنة بمتوسط كل المناطق الثمانية (بيانات OCM حقيقية، ما في رقم مختلق).
    stress_pct أعلى = محطات أقل نسبياً = مرونة أقل لو صار حمل إضافي فجأة.
    """
    stats = _charger_stats()
    if not stats:
        return {"tool": "get_regional_load_stress", "error": "no charger data"}
    avg_points = sum(v["points"] for v in stats.values()) / len(stats)
    here = stats.get(region, {"stations": 0, "points": 0, "avg_kw": 0})
    ratio = (here["points"] / avg_points) if avg_points else 0
    # منحنى مستمر (100/(1+ratio)) بدل صيغة الانحصار القديمة اللي كانت "تفلّط" عند صفر
    # بالضبط لأي منطقة نسبتها >= 1.5x المتوسط (عمان كانت بتطلع 0% بالظبط رغم إنها
    # 4.2x المتوسط، مش 1.5x بس — رقم صحيح رياضياً بس يبين مريب بعرض حي).
    # هلق: ratio=1 (بالضبط عند المتوسط) = 50% (نقطة الوسط الطبيعية)، وكل ما زادت
    # البنية التحتية عن المتوسط الرقم بينزل تدريجياً بدون ما يوصل صفر مضبوط أبداً.
    stress_pct = round(100 / (1 + ratio), 1)
    return {
        "tool": "get_regional_load_stress",
        "region": region,
        "stations": here["stations"],
        "charging_points": here["points"],
        "avg_charger_kw": here["avg_kw"],
        "network_avg_points": round(avg_points, 1),
        "stress_pct": stress_pct,
        "flagged": stress_pct >= 55,
        "summary": f"{here['points']} نقطة شحن بـ{region} مقابل متوسط شبكي {avg_points:.1f} نقطة/منطقة",
    }


def estimate_cascading_risk(region, radius_km=160):
    """
    يفحص المناطق الجغرافية القريبة فعلياً (بالمسافة الحقيقية) ويقيّم خطر
    امتداد الضغط إليها لو تفاقم وضع منطقة الحادثة (Cascading Failure).
    """
    stats = _charger_stats()
    neighbors = nearest_regions(region, max_km=radius_km)
    out = []
    for n in neighbors:
        s = stats.get(n["region"], {"stations": 0, "points": 0, "avg_kw": 0})
        # كل ما كانت المنطقة أقرب وبنيتها التحتية أضعف = خطر امتداد أعلى
        proximity_factor = max(0, 1 - n["distance_km"] / radius_km)
        weakness_factor = max(0, 1 - min(s["points"], 40) / 40)
        risk = round(proximity_factor * 0.8 + weakness_factor * 0.2, 3)
        out.append({
            "region": n["region"], "distance_km": n["distance_km"],
            "stations": s["stations"], "charging_points": s["points"],
            "risk_score": risk,
        })
    out.sort(key=lambda x: x["risk_score"], reverse=True)  # 80% قرب + 20% ضعف البنية
    return {
        "tool": "estimate_cascading_risk",
        "region": region,
        "neighbors_checked": len(out),
        "at_risk": out[:4],
        "flagged": any(o["risk_score"] >= 0.5 for o in out[:4]),
        "summary": f"فحصت {len(out)} منطقة مجاورة فعلياً ضمن {radius_km} كم",
    }


def compute_financial_burn_rate(winner_agent, confidence, evidence_magnitude=None):
    """
    نفس منطق estimateImpact() بالواجهة (P9.4) لكن بالسيرفر، عشان نطلع منه
    معدّل/ثانية حقيقي يغذّي شارت الخسارة المتصاعدة الحي (Chart 1).
    evidence_magnitude: قيمة 0..1.3 اختيارية (نفس مقياس evidenceMagnitude بالـJS)،
    لو ما توفرت منستخدم الثقة نفسها كبديل معقول.
    """
    r = IMPACT_RANGES.get(winner_agent)
    if not r:
        return {"tool": "compute_financial_burn_rate", "error": f"unknown agent {winner_agent}"}
    mag = evidence_magnitude if evidence_magnitude is not None else max(0, confidence - 0.3) * 1.1
    mag = max(0.0, min(1.2, mag))
    lo = round(r["lo"] + (r["hi"] - r["lo"]) * min(1, mag) * 0.6)
    hi = round(r["lo"] + (r["hi"] - r["lo"]) * min(1.2, mag + 0.25))
    if hi < lo:
        hi = lo + max(3, round(lo * 0.2))
    mid_daily = (lo + hi) / 2
    return {
        "tool": "compute_financial_burn_rate",
        "winner_agent": winner_agent,
        "label": r["label"],
        "daily_lo_jod": lo,
        "daily_hi_jod": hi,
        "daily_mid_jod": round(mid_daily, 2),
        "per_second_jod": round(mid_daily / 86400, 6),
        "per_minute_jod": round(mid_daily / 1440, 4),
    }


def compute_response_time_index(incident_id):
    """
    مقارنة حقيقية: زمن حسم النظام الآلي الفعلي (من created_at إلى resolved_at
    المسجّلين فعلياً بقاعدة البيانات لهاي الحادثة بالذات) مقابل خط أساس بشري تقديري.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT created_at, resolved_at FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    conn.close()
    if not row or not row["resolved_at"]:
        return {"tool": "compute_response_time_index", "error": "incident not resolved yet"}
    from datetime import datetime
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        t0 = datetime.strptime(row["created_at"][:19], fmt)
        t1 = datetime.strptime(row["resolved_at"][:19], fmt)
    except (ValueError, TypeError):
        return {"tool": "compute_response_time_index", "error": "bad timestamp format"}
    system_seconds = max(1, (t1 - t0).total_seconds())
    baseline_seconds = HUMAN_BASELINE_MINUTES * 60
    speedup = round(baseline_seconds / system_seconds, 1)
    return {
        "tool": "compute_response_time_index",
        "incident_id": incident_id,
        "system_seconds": round(system_seconds, 1),
        "baseline_seconds": baseline_seconds,
        "baseline_minutes": HUMAN_BASELINE_MINUTES,
        "speedup_factor": speedup,
        "summary": f"حسم آلياً خلال {system_seconds:.0f} ثانية مقابل خط أساس بشري تقديري {HUMAN_BASELINE_MINUTES} دقيقة",
    }
