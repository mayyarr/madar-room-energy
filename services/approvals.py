"""
services/approvals.py - جسر الموافقة البشرية (P5.3)

بعد ما يحسم الـ Arbitrator حادثة (status="resolved")، هاد الملف يحدد:
1. شو الإجراء الميداني المقترح بناء على الوكيل الفائز (ACTION_CATALOG).
2. مستوى خطورته (risk_level) بناء على نوع الإجراء وقوة الثقة النهائية.
3. يسجله بقاعدة البيانات كـ action + approval معلّقة (pending) — ما بينفذ شي فعلياً،
   بس بيجهز الطلب لحد يوافق أو يرفض (إنسان حقيقي، عبر CLI الآن، وعبر زر بالواجهة لاحقاً P6).

لا يستدعي Groq أبداً — كله قرارات بالكود بناء على اسم الوكيل ومستوى الثقة، فما بيستهلك
كوتا اليومية. هاد مهم لأنه ممكن نختبره ونطوّره بحرية حتى لو الكوتا مستنفدة.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import database as db  # noqa: E402

# ---------- 1. كتالوج الإجراءات: كل وكيل فائز بيقترح نوع إجراء مختلف ----------
# risk_level هون هو الافتراضي؛ ممكن يرتفع حسب قوة الثقة (انظر _risk_for).

ACTION_CATALOG = {
    "Meter Agent": {
        "action_type": "meter_inspection",
        "description_template": "إرسال فني لفحص العداد ميدانياً — قد يكون عطل بالعداد أو استهلاك غير مصرح به. الفرضية: {hypothesis}",
        "base_risk": "medium",
    },
    "Charging Agent": {
        "action_type": "charging_load_action",
        "description_template": "تقييد أو إيقاف مؤقت لحمل شحن المركبات الكهربائية بالمنطقة ريثما يتم التحقق الميداني. الفرضية: {hypothesis}",
        "base_risk": "high",
    },
    "Plant Agent": {
        "action_type": "maintenance_dispatch",
        "description_template": "جدولة صيانة أو إيقاف تشغيلي جزئي للمعدة بسبب مؤشرات تدهور. الفرضية: {hypothesis}",
        "base_risk": "high",
    },
}

# لو الثقة النهائية عالية جداً (>= هاد الحد) والإجراء الأساسي "medium"، نرفعه لـ "high"
# لأنه كلما زادت ثقتنا بالقرار، الأثر المتوقع للتنفيذ الخاطئ (لو غلط) بيصير أهم نراجعه بعناية أكبر
# لا أقل، لأن قرارات عالية الثقة غالباً تُنفَّذ أسرع.
HIGH_CONFIDENCE_RISK_BUMP = 0.75


def _risk_for(base_risk, final_confidence):
    """يرفع مستوى الخطورة درجة وحدة إذا الثقة عالية جداً (يعني قرار حاسم، تأثيره أكبر)."""
    order = ["low", "medium", "high"]
    idx = order.index(base_risk)
    if final_confidence >= HIGH_CONFIDENCE_RISK_BUMP and idx < len(order) - 1:
        idx += 1
    return order[idx]


def propose_action(incident_id, winner_agent, hypothesis, final_confidence):
    """
    (2) تُستدعى بعد ما incident يُحسم بـ status="resolved" (من run_and_save بالـ Arbitrator).
    تبني اقتراح إجراء من ACTION_CATALOG، تحفظه بجدول actions، وتفتح طلب موافقة معلّق.
    ترجع dict فيه كل التفاصيل المفيدة للواجهة أو الـ CLI.

    لو الوكيل الفائز مش موجود بالكتالوج (حالة نادرة)، ما في اقتراح إجراء — نرجّع None
    بدل ما نخترع إجراء، اتساقاً مع مبدأ "لا تخترعي نتيجة" (4.5).
    """
    entry = ACTION_CATALOG.get(winner_agent)
    if entry is None:
        return None

    risk_level = _risk_for(entry["base_risk"], final_confidence)
    description = entry["description_template"].format(hypothesis=hypothesis[:300])

    action_id = db.insert_action(
        incident_id, action_type=entry["action_type"],
        description=description, risk_level=risk_level,
    )
    approval_id = db.request_approval(
        incident_id, action_id, action_text=description, risk_level=risk_level,
    )

    return {
        "incident_id": incident_id,
        "action_id": action_id,
        "approval_id": approval_id,
        "action_type": entry["action_type"],
        "description": description,
        "risk_level": risk_level,
        "final_confidence": final_confidence,
        "status": "pending",
    }


# ---------- 3. قرار الإنسان: موافقة أو رفض ----------

def approve(approval_id, decided_by=None, comment=None):
    """إنسان وافق على الإجراء. decided_by اسم الشخص (اختياري، للسجل فقط)."""
    db.decide_approval(approval_id, status="approved", decided_by=decided_by, comment=comment)
    return {"approval_id": approval_id, "status": "approved"}


def reject(approval_id, decided_by=None, comment=None):
    """إنسان رفض الإجراء. الحادثة تبقى محفوظة بقاعدة البيانات، بس الإجراء ما بينفذ."""
    db.decide_approval(approval_id, status="rejected", decided_by=decided_by, comment=comment)
    return {"approval_id": approval_id, "status": "rejected"}


def get_pending(incident_id=None):
    """كل الموافقات المعلّقة (لحادثة معينة، أو كلها إذا incident_id=None). تُستخدم بالواجهة لاحقاً (P6)."""
    return db.list_pending_approvals(incident_id)


if __name__ == "__main__":
    # اختبار يدوي سريع بدون Groq: python services/approvals.py
    # يبني حادثة وهمية بسيطة ويجرب propose_action -> get_pending -> approve
    incident_id = db.insert_incident(
        title="اختبار موافقات", description="سيناريو تجريبي لموديول approvals.py",
        severity="medium", region="Madaba",
    )
    result = propose_action(
        incident_id, winner_agent="Charging Agent",
        hypothesis="شاحن مركبة كهربائية بدأ يعمل حديثاً ويطابق ارتفاع العداد بنسبة عالية.",
        final_confidence=0.80,
    )
    print("=== ACTION PROPOSED ===")
    print(result)

    print("\n=== PENDING (before decision) ===")
    print(get_pending(incident_id))

    decision = approve(result["approval_id"], decided_by="test_user", comment="تم التحقق ميدانياً، موافق")
    print("\n=== DECISION ===")
    print(decision)

    print("\n=== PENDING (after decision, should be empty) ===")
    print(get_pending(incident_id))