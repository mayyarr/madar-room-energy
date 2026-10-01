"""
case_memory.py - "النظام بيتذكر حالاته السابقة" (يشبه فكرة التعلّم من التجارب،
بس بنسخة بسيطة وصريحة وقابلة للتفسير: مقارنة نصية + هيكلية مع الحوادث المغلقة
السابقة، بدون أي "سحر" أو استدعاء نموذج إضافي).

المنطق:
1. لو نص الحادثة الجديدة فيه نفس meter_id / unit_id / charger_id بالضبط يلي
   ظهر بوصف حادثة سابقة مغلقة -> تطابق شبه مؤكد (score=1.0، match_type="exact_id").
2. غير هيك، نحسب تشابه نصي (difflib) بين الوصفين + بونص لو نفس المنطقة (region)
   -> score تناسبي حقيقي، مش تخمين.
نرجع أفضل تطابق فوق حد أدنى (SIMILARITY_THRESHOLD)، مع رقم الحادثة القديمة
ونتيجتها الفعلية (status, winner_agent, final_confidence) عشان الواجهة تعرضها
كـ"شايف حالة شبيهة قبل هيك: رقم كذا، وطلعت كذا".
"""
import re
from difflib import SequenceMatcher

from db.database import list_incidents

SIMILARITY_THRESHOLD = 0.42          # تحت هاد الرقم ما نعتبرها تشابه ذو معنى
REGION_MATCH_BONUS = 0.15            # بونص لو نفس المنطقة بالضبط
ID_PATTERN = re.compile(r"\b([A-Za-z]{2,}\d{3,}|unit\s*\d+)\b", re.IGNORECASE)


def _extract_ids(text):
    """يطلع أي شي يشبه meter_id (MACxxxxxx) أو unit N أو charger id من النص."""
    if not text:
        return set()
    return {m.group(1).lower().replace(" ", "") for m in ID_PATTERN.finditer(text)}


def _text_similarity(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def find_similar_case(description, region=None, exclude_incident_id=None):
    """
    يدوّر بكل الحوادث المغلقة سابقاً (استُبعد الحدث الحالي نفسه لو انحفظ أصلاً)
    ويرجّع أفضل تطابق، أو None لو ما في شي فوق العتبة.
    """
    new_ids = _extract_ids(description)
    past = [
        inc for inc in list_incidents()
        if inc["id"] != exclude_incident_id and inc.get("status") in ("resolved", "undecided")
        and inc.get("description")
    ]

    best, best_score, best_reason = None, 0.0, None

    for inc in past:
        # 1) تطابق مباشر بمعرّف حقيقي (عداد/محرك/شاحن) -> شبه مؤكد
        past_ids = _extract_ids(inc.get("description", ""))
        shared_ids = new_ids & past_ids
        if shared_ids:
            score = 1.0
            reason = f"نفس المعرّف بالضبط: {', '.join(sorted(shared_ids))}"
            if score > best_score:
                best, best_score, best_reason = inc, score, reason
            continue

        # 2) تشابه نصي + بونص نفس المنطقة
        score = _text_similarity(description, inc.get("description", ""))
        if region and inc.get("region") and region == inc["region"]:
            score = min(1.0, score + REGION_MATCH_BONUS)
        if score > best_score:
            best, best_score = inc, score
            best_reason = "تشابه بوصف الحادثة" + (f" + نفس المنطقة ({region})" if region and inc.get("region") == region else "")

    if best is None or best_score < SIMILARITY_THRESHOLD:
        return None

    return {
        "incident_id": best["id"],
        "title": best.get("title"),
        "region": best.get("region"),
        "similarity": round(float(best_score), 2),
        "reason": best_reason,
        "status": best.get("status"),
        "winner_agent": best.get("winner_agent"),
        "final_confidence": best.get("final_confidence"),
    }
