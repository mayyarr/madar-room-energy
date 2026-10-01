import math


def _is_signal(output):
    """هل هذه الأداة دليل داعم؟ أداة تنفي الفرضية (refutes) لا تُحسب أبداً كدليل داعم،
    حتى لو رفعت flagged/new_anomaly."""
    if output.get("refutes"):
        return False
    return bool(output.get("flagged") or output.get("new_anomaly"))


def _combine(strengths):
    """ندمج أقوى دليلين فقط بطريقة noisy-OR، بدل كل الأدلة.
    هيك دليل واحد قوي يعطي ثقة عالية، وكثرة الأدلة المتوسطة لا تصنع ثقة مصطنعة."""
    top2 = sorted(strengths, reverse=True)[:2]
    remaining = math.prod(1 - s for s in top2)
    return 1 - remaining


# أدوات دليلها "غير مباشر" (تطابق حجم أو نمط، بدون تأكيد فيزيائي/مكاني)
INDIRECT_TOOLS = {"explain_meter_change"}
INDIRECT_DISCOUNT = 0.7   # خصم 20%

CONFIDENCE_CAP = 0.90     # لا وكيل يصل لثقة كاملة


def compute_confidence(evidence):
    """
    يحسب ثقة الوكيل (0 إلى 1) بأن هناك مشكلة حقيقية في مجاله،
    اعتماداً على أدلة الأدوات فقط وليس على كلام الموديل.

    القواعد:
    - أداة refutes لا تُحسب كدليل داعم أبداً، حتى لو flagged.
    - ندمج أقوى دليلين فقط (top-2 noisy-OR)، لا كل الأدلة.
    - سقف أعلى 0.90.
    - خصم 20% إذا كان أقوى دليل من نوع "تطابق حجم فقط" (INDIRECT_TOOLS).
    - إذا وُجد أي دليل refutes تنخفض الثقة النهائية إلى 30% من قيمتها.
    - الأدوات الفاشلة (error) تُتجاهل تماماً (بالاسم والنتيجة معاً).
    - بدون أي إشارة داعمة تبقى الثقة منخفضة جداً (0.05).
    """
    valid = [e["output"] for e in evidence if "error" not in e["output"]]
    signals = [(e["tool"], e["output"]) for e in evidence
               if "error" not in e["output"] and _is_signal(e["output"])]

    if not signals:
        return 0.05

    strengths = [float(o.get("strength", 0)) for _, o in signals]
    confidence = _combine(strengths)

    strongest_tool = max(signals, key=lambda t: float(t[1].get("strength", 0)))[0]
    if strongest_tool in INDIRECT_TOOLS:
        confidence *= INDIRECT_DISCOUNT

    if any(o.get("refutes") for o in valid):
        confidence *= 0.3

    confidence = min(confidence, CONFIDENCE_CAP)
    return round(max(0.05, confidence), 2)


# الفكرة: نأخذ أقوى دليلين فقط (لا كل الأدلة)، فدليل واحد قوي وواضح يعطي ثقة عالية
# دون الحاجة لخمس أدوات متوسطة. سقف 0.90 لأن أدوات الإحصاء لا تثبت السبب 100%.
# خصم 20% للأدلة غير المباشرة (تطابق حجم بدون موقع). أداة refutes تُستبعد من الدعم
# وتُخفّض الثقة النهائية، بدل أن "تدعم وتنفي" بنفس الوقت.