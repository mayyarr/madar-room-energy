"""
arbitrator.py - وكيل الحكم (Arbitrator)
بيوزع الحدث الواحد على الوكلاء الثلاثة (Meter, Plant, Charging) بالتوازي،
كل وحدة بتشتغل بخيط (thread) منفصل لأن أطول شي بكل وكيل هو انتظار رد Groq
عبر الشبكة، مش حساب على المعالج، فالـ threads كافية (ما في داعي لـ multiprocessing).

4.1: dispatch()          - التوزيع والتجميع
4.2: compare_confidence() - مقارنة الثقة وتحديد "مجموعة التعادل" (2 أو 3 وكلاء)
4.3: debate_round()       - مواجهة حقيقية بين كل وكلاء المجموعة (round robin كامل)
"""
import os
import sys
import json
from concurrent.futures import ThreadPoolExecutor, as_completed


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config                                                # noqa: E402
from groq import Groq                                        # noqa: E402
from services.confidence import compute_confidence            # noqa: E402
from db import database as db                                # noqa: E402
from agents.meter_agent import create_meter_agent, SYSTEM_PROMPT as METER_PROMPT        # noqa: E402
from agents.plant_agent import create_plant_agent, SYSTEM_PROMPT as PLANT_PROMPT        # noqa: E402
from agents.charging_agent import create_charging_agent, SYSTEM_PROMPT as CHARGING_PROMPT  # noqa: E402
from services import approvals  # noqa: E402
from services.case_memory import find_similar_case  # noqa: E402
from agents.geo_agent import run_geo_assessment  # noqa: E402
from services import experience  # noqa: E402
import time as _time  # noqa: E402
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

AGENT_PROMPTS = {
    "Meter Agent": METER_PROMPT,
    "Plant Agent": PLANT_PROMPT,
    "Charging Agent": CHARGING_PROMPT,
}
# 4.5: لو أعلى ثقة بالنتيجة أقل من هاد الرقم، الحدث "غير محسوم" حتى لو ما صار
# تعادل تقني (يعني حتى لو الفرق كبير، الفايز نفسه ما عنده دليل كافي).
MIN_WINNING_CONFIDENCE = 0.30  # الفوز لازم يكون أعلى من هاد الحد بشكل صريح (0.30 بالضبط = غير محسوم)


def _run_one(create_fn, event_text, emit):
    """
    يبني وكيل واحد ويشغّله. أي استثناء (فشل Groq، أداة عطلانة...) ما بيوقف
    الوكلاء الباقيين — منرجعه كنتيجة "فشل/غير محسوم" بدل ما نكسر كل شي.
    """
    agent = create_fn()
    try:
        return agent.run(event_text, emit=emit)
    except Exception as e:
        emit({"agent": agent.name, "type": "error", "error": str(e)})
        return {
            "agent": agent.name,
            "hypothesis": f"Inconclusive: agent failed ({e})",
            "confidence": 0.0,
            "evidence": [],
            "failed": True,
        }


def dispatch(event_text, emit=print):
    """
    يشغّل الوكلاء الثلاثة بالتوازي عبر ThreadPoolExecutor.
    يرجع dict بالشكل:
    {"Meter Agent": result, "Plant Agent": result, "Charging Agent": result}
    كل result بشكل العقد: {"agent", "hypothesis", "confidence", "evidence"}
    """
    creators = [create_meter_agent, create_plant_agent, create_charging_agent]
    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_run_one, fn, event_text, emit): fn for fn in creators}
        for future in as_completed(futures):
            result = future.result()  # ما بترمي استثناء، _run_one ملفوفة بـ try/except
            results[result["agent"]] = result
    return results


def compare_confidence(results):
    """
    يرتّب الوكلاء حسب الثقة. أي وكيل ثقته ضمن TIE_THRESHOLD من صاحب أعلى ثقة
    يدخل "مجموعة التعادل" (tied_group)، ويشارك بالنقاش لاحقاً — بغض النظر إذا
    كانوا اثنين أو ثلاثة. لو المجموعة فيها وكيل واحد فقط -> فوز واضح (clear).
    """
    ranked = sorted(results.items(), key=lambda kv: kv[1]["confidence"], reverse=True)

    if not ranked:
        return {"status": "clear", "ranked": [], "leader": None,
                "tied_group": [], "gap": None}

    top_confidence = ranked[0][1]["confidence"]
    tied_group = [name for name, res in ranked
                  if top_confidence - res["confidence"] < config.TIE_THRESHOLD]

    if len(tied_group) < 2:
        return {"status": "clear", "ranked": ranked, "leader": ranked[0][0],
                "tied_group": tied_group, "gap": None}

    tied_confidences = [results[n]["confidence"] for n in tied_group]
    gap = round(max(tied_confidences) - min(tied_confidences), 4)

    return {
        "status": "tie",
        "ranked": ranked,
        "leader": ranked[0][0],
        "tied_group": tied_group,   # كل الوكلاء المتقاربين معاً (2 أو 3)
        "gap": gap,
    }


# ---------- 4.3: مواجهة حقيقية بين كل وكلاء مجموعة التعادل ----------

DEBATE_INSTRUCTIONS = """
You are now in a debate round against another investigating agent. You already gave
your hypothesis and evidence for this incident. A competing agent gave a different
hypothesis with a similarly close confidence.

Your task: look ONLY at the competing agent's evidence (tool name + one-line summary
for each) and decide which of THEIR evidence items, if any, you can seriously
challenge as weak, indirect, or not real proof of their conclusion. Only challenge
evidence you can honestly justify challenging. Do not challenge everything just to
"win" the debate — an unfair challenge helps no one.

Respond with ONLY a JSON object, no other text, no markdown, in exactly this form:
{"challenged_tools": ["tool_name_1", "tool_name_2"], "reason": "one or two sentences explaining your strongest objection"}

If you believe their evidence is solid and you have no valid objection, respond:
{"challenged_tools": [], "reason": "no valid objection"}
"""

_debate_client = Groq(api_key=config.GROQ_API_KEY)


def _challenge(agent_name, own_result, opponent_name, opponent_result, emit):
    """
    استدعاء واحد خفيف (مش عبر Agent.run الكامل، ما في داعي لأدوات هون) يطلب من
    وكيل واحد يفحص أدلة الوكيل التاني ويحدد أي أداة منها ضعيفة كدليل.
    """
    evidence_lines = "\n".join(
        f"- {e['tool']}: {e['output'].get('summary', '')}"
        for e in opponent_result["evidence"]
        if "error" not in e.get("output", {})
    ) or "(no evidence)"

    user_msg = (
        f"Your hypothesis: {own_result['hypothesis'][:400]}\n\n"
        f"Competing agent ({opponent_name})'s hypothesis: {opponent_result['hypothesis'][:400]}\n\n"
        f"Their evidence:\n{evidence_lines}\n\n"
        "Which of their evidence items (by exact tool name) can you seriously challenge? "
        "Respond with ONLY JSON as instructed."
    )
    system = AGENT_PROMPTS.get(agent_name, "") + "\n\n" + DEBATE_INSTRUCTIONS

    challenged, reason = [], "no valid objection"
    rp = experience.next_replay_challenge(agent_name, opponent_name)
    if rp is not None:
        # من الخبرة: نفس التحدي يلي صار بالحالة المطابقة السابقة (بدون نداء LLM)
        _time.sleep(0.35)
        emit({"agent": agent_name, "type": "challenge", "target": opponent_name,
              "challenged_tools": rp["challenged_tools"], "reason": rp["reason"]})
        return {"by": agent_name, "against": opponent_name,
                "challenged_tools": rp["challenged_tools"], "reason": rp["reason"]}
    try:
        resp = _debate_client.chat.completions.create(
            model=config.MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user_msg}],
            temperature=0.1,
        )
        text = (resp.choices[0].message.content or "").strip().strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)
        challenged = [t for t in data.get("challenged_tools", []) if isinstance(t, str)]
        reason = str(data.get("reason", ""))[:300]
    except Exception as e:
        reason = f"(challenge call failed, no penalty applied: {e})"

    emit({"agent": agent_name, "type": "challenge", "target": opponent_name,
          "challenged_tools": challenged, "reason": reason})
    return {"by": agent_name, "against": opponent_name,
            "challenged_tools": challenged, "reason": reason}


# أي دليل ينتحدّى بنجاح بيخسر نص قوته بدل ما ينحذف كامل. الحذف الكامل كان يخلي
# جملة وحدة من الموديل تمسح كل أدلة الخصم (مثلاً 0.60 -> 0.05)، فالنتيجة تتقلب بين
# تشغيلة وتشغيلة. هيك الدليل القوي بيضل قوي حتى لو انتحدّى، والمتقارب ممكن ينقلب.
CHALLENGE_PENALTY = 0.5


def _apply_challenge(evidence, challenged_tools):
    """يرجع نسخة من الأدلة، والأدوات يلي انتحدّت قوتها (strength) مضروبة بـ CHALLENGE_PENALTY."""
    if not challenged_tools:
        return evidence
    out = []
    for e in evidence:
        if e["tool"] in challenged_tools and isinstance(e.get("output"), dict) \
                and "strength" in e["output"]:
            weakened = dict(e["output"])
            weakened["strength"] = round(float(weakened.get("strength") or 0) * CHALLENGE_PENALTY, 3)
            weakened["challenged"] = True
            e = {**e, "output": weakened}
        out.append(e)
    return out


def _challenge_round(group, evidence_by_name, results, emit, ceiling=None):
    """
    جولة تحدٍّ واحدة (round robin كامل) بين كل وكلاء group الحاليين، فوق
    الأدلة المتبقية عندهم لهلق (evidence_by_name — تتغيّر بين الجولات لو صار
    إقصاء). ترجع (challenges, confidence_after_dict, remaining_evidence_by_name).
    """
    challenges = []
    challenged_tools_by_target = {name: set() for name in group}

    for challenger_name in group:
        # الوكيل بيتحدى بأدلته الأصلية (نفس منطق اليوم)، بس بيهاجم أدلة الخصم
        # المتبقية بعد أي إقصاء سابق
        challenger_result = dict(results[challenger_name])
        for target_name in group:
            if challenger_name == target_name:
                continue
            target_result = dict(results[target_name])
            target_result["evidence"] = evidence_by_name[target_name]
            c = _challenge(challenger_name, challenger_result,
                            target_name, target_result, emit)
            challenges.append(c)
            challenged_tools_by_target[target_name].update(c["challenged_tools"])

    remaining = {}
    confidence_after = {}
    for name in group:
        remaining[name] = _apply_challenge(
            evidence_by_name[name], list(challenged_tools_by_target[name])
        )
        new_conf = compute_confidence(remaining[name])
        # التحدي بيضعّف أو ما بيغيّر، بس ما بيقدر يرفع الثقة أبداً. بدون هالسقف، إضعاف
        # دليل "غير مباشر" كان يشيل خصم INDIRECT_DISCOUNT عن الثقة الكلية فتطلع (0.67 -> 0.80).
        cap = (ceiling or {}).get(name, results[name]["confidence"])
        confidence_after[name] = min(new_conf, cap)
    return challenges, confidence_after, remaining


def debate_round(results, decision, event_text, emit=print):
    """
    مواجهة حقيقية بين كل وكلاء مجموعة التعادل (اثنين أو ثلاثة)، مش بس بين
    أعلى اثنين. كل وكيل يتحدى كل وكيل آخر بالمجموعة (round robin كامل):
    لو المجموعة فيها 3، بيصير 6 نداءات تحدٍّ بالجولة الأولى (كل وكيل × كل وكيل آخر).

    بحالة الثلاثة: الجولة الأولى تحدٍّ جماعي بين الثلاثة سوا. بعدها الأضعف —
    يلي طلعت ثقته أوطى وحدة بعد التحدي — يُقصى ("weakest after first round"،
    مش نفس معاملة الخاسر العادي، بل إقصاء صريح وواضح بالسجل). الاتنين الباقيين
    يكملوا عادي: لو الفرق بينهم صار كافي (>= TIE_THRESHOLD) بعد الجولة الأولى،
    القرار ينحسم فوراً. لو لسا متقاربين، تصير جولة تحدٍّ ثانية بينهم هما الاتنين
    بس (فوق الأدلة المتبقية بعد الجولة الأولى)، وعلى أساسها ينحسم القرار أو
    يضل تعادل. أي أداة تحدّاها أي خصم بنجاح تُستبعد من أدلة صاحبها، وتُعاد
    حساب الثقة بالكود (compute_confidence نفسها، لا كلام الموديل).
    """
    if decision["status"] != "tie":
        return None

    group = decision["tied_group"]
    # نبعت مجموعة التعادل الحقيقية كاملة (اثنين أو ثلاثة) قبل ما يبلش أي تحدٍّ،
    # عشان الواجهة تفتح ساحة المواجهة بكل الوكلاء من البداية.
    emit({"type": "tie_detected", "group": group, "gap": decision["gap"]})

    confidence_before = {name: results[name]["confidence"] for name in group}
    evidence_by_name = {name: results[name]["evidence"] for name in group}

    all_challenges, confidence_after, evidence_by_name = _challenge_round(
        group, evidence_by_name, results, emit
    )

    eliminated = None
    final_group = group

    if len(group) > 2:
        # الأضعف بعد الجولة الجماعية الأولى يُقصى صراحة، والاتنين الباقيين يكملوا
        weakest = min(group, key=lambda n: confidence_after[n])
        eliminated = {
            "agent": weakest,
            "confidence_after_round1": confidence_after[weakest],
            "reason": "Weakest after the first three-way challenge round",
        }
        emit({"type": "eliminated", **eliminated})
        final_group = [n for n in group if n != weakest]

        remaining_gap = round(
            max(confidence_after[n] for n in final_group)
            - min(confidence_after[n] for n in final_group), 4,
        )
        if remaining_gap < config.TIE_THRESHOLD:
            # لسا الاتنين الباقيين متقاربين — جولة تحدٍّ ثانية بينهم هما بس،
            # فوق الأدلة المتبقية بعد الجولة الأولى (مش الأدلة الأصلية)
            emit({"type": "second_round_tie", "group": final_group, "gap": remaining_gap})
            round2_evidence = {n: evidence_by_name[n] for n in final_group}
            round2_challenges, round2_confidence, _ = _challenge_round(
                final_group, round2_evidence, results, emit,
                ceiling={n: confidence_after[n] for n in final_group},
            )
            all_challenges += round2_challenges
            for n in final_group:
                confidence_after[n] = round2_confidence[n]

    max_conf = max(confidence_after[n] for n in final_group)
    min_conf = min(confidence_after[n] for n in final_group)
    still_tied = (max_conf - min_conf) < config.TIE_THRESHOLD
    winner = None if still_tied else max(final_group, key=lambda n: confidence_after[n])

    outcome = {
        "status": "still_tied" if still_tied else "resolved",
        "winner": winner,
        "group": group,
        "final_group": final_group,
        "eliminated": eliminated,
        "confidence_before": confidence_before,
        "confidence_after": confidence_after,
        "gap_after": round(max_conf - min_conf, 4),
        "challenges": all_challenges,
    }
    emit({"type": "debate_result", **outcome})
    return outcome


# ---------- 4.4: الحفظ الكامل بقاعدة البيانات ----------

def _emit_and_trace(incident_id, save_trace, user_emit):
    """يغلّف emit الأصلية: تطبع متل العادة، وكمان تحفظ كل حدث كـ trace_event."""
    def emit(d):
        user_emit(d)
        if save_trace:
            try:
                db.insert_trace_event(incident_id, d.get("agent", "Arbitrator"),
                                       d.get("type", "event"), d)
            except Exception as e:
                user_emit({"type": "trace_save_error", "error": str(e)})
    return emit


def run_and_save(event_text, title=None, region=None, severity=None,
                  event_time=None, emit=print, save_trace=True,
                  force_three_way=False):
    """
    التدفق الكامل: يفتح حدث، يشغّل الوكلاء الثلاثة، يقارن الثقة، يعمل مواجهة
    حقيقية بين كل وكلاء التعادل (2 أو 3)، ويحفظ كل شيء (agent_runs, hypotheses,
    evidence, arbitration_log, trace_events)، ثم يقفل الحدث بحالة نهائية.
    يرجع dict فيه incident_id والنتائج والقرار وتفاصيل النقاش (إن وُجد).

    force_three_way=True: خيار عرض توضيحي فقط (لا يُستخدم بالمسار الافتراضي ولا
    بالسيناريو الحر) — بيضم كل الوكلاء الناجحين (مش بس يلي ضمن TIE_THRESHOLD)
    بمجموعة النقاش، عشان تصير مواجهة round-robin كاملة بين الثلاثة فعلياً وقت
    العرض الحي. الأدلة والثقات نفسها 100% حقيقية ومحسوبة بنفس compute_confidence
    العادية — الشي الوحيد المتغيّر هو مين يدخل النقاش، مش قيمة أي دليل.
    """
    incident_id = db.insert_incident(
        title=title or event_text[:80],
        description=event_text,
        severity=severity,
        region=region,
        event_time=event_time,
    )
    traced_emit = _emit_and_trace(incident_id, save_trace, emit)

    # ذاكرة الحالات: هل شبه هاد السيناريو صار قبل هيك؟ لو لقينا حالة سابقة
    # قريبة (نفس المعرّف الفعلي، أو تشابه نصي قوي + نفس المنطقة)، نبعت حدث
    # للواجهة قبل ما نبلش التحقيق، مع رقم الحالة ونتيجتها الفعلية السابقة.
    try:
        similar = find_similar_case(event_text, region=region, exclude_incident_id=incident_id)
    except Exception as e:
        similar = None
        traced_emit({"type": "similar_case_error", "error": str(e)})
    if similar:
        traced_emit({"type": "similar_case", **similar})

    agent_names = ["Meter Agent", "Plant Agent", "Charging Agent"]
    run_ids = {name: db.start_agent_run(incident_id, name, round=1) for name in agent_names}

    t_start = _time.time()
    lesson = None
    results = None
    try:
        lesson = experience.lookup(event_text, region)
        if lesson:
            results = experience.replay(lesson, traced_emit)
    except Exception:  # noqa: BLE001
        results = None
    if results is not None:
        experience.set_replay_challenges(lesson["challenges"])
        experience.mark_used(lesson["key"])
    else:
        lesson = None
        experience.clear_replay_challenges()
        results = dispatch(event_text, emit=traced_emit)

    hyp_ids = {}
    for name, result in results.items():
        run_id = run_ids.get(name)
        if result.get("failed"):
            db.finish_agent_run(run_id, "error", error_message=result["hypothesis"])
            continue
        hyp_ids[name] = db.save_agent_result(incident_id, run_id, result, round=1)

    if force_three_way:
        ranked = sorted(results.items(), key=lambda kv: kv[1]["confidence"], reverse=True)
        active = [name for name, res in ranked if not res.get("failed")]
        if len(active) >= 2:
            confidences = [results[n]["confidence"] for n in active]
            decision = {
                "status": "tie", "ranked": ranked, "leader": ranked[0][0],
                "tied_group": active,
                "gap": round(max(confidences) - min(confidences), 4),
            }
        else:
            decision = compare_confidence(results)
    else:
        decision = compare_confidence(results)
    # الوكيل الفاشل (خطأ Groq / rate limit / chaos) ما بيدخل المقارنة ولا المواجهة —
    # ثقته 0 مش لأنه ما لقى دليل، لأنه ما اشتغل أصلاً. قبل هيك كان يدخل التعادل كمنافس.
    if decision["status"] == "tie":
        live = [n for n in decision["tied_group"] if not results[n].get("failed")]
        if len(live) < 2:
            decision = compare_confidence({n: r for n, r in results.items() if not r.get("failed")}) \
                if any(not r.get("failed") for r in results.values()) else \
                {"status": "clear", "ranked": [], "leader": None, "tied_group": [], "gap": None}
        else:
            decision = dict(decision, tied_group=live)
    debate = None

    if decision["status"] == "tie":
        group = decision["tied_group"]
        db.insert_arbitration_log(
            incident_id, event_type="tie_detected",
            message=f"{len(group)}-way tie: {', '.join(group)} (gap={decision['gap']})",
            round=1,
            confidence_before={n: results[n]["confidence"] for n in group},
        )
        debate = debate_round(results, decision, event_text, emit=traced_emit)

        for ch in debate["challenges"]:
            db.insert_arbitration_log(
                incident_id, event_type="challenge", message=ch["reason"], round=1,
                challenger_agent=ch["by"], challenged_agent=ch["against"],
            )

        db.insert_arbitration_log(
            incident_id, event_type="debate_result",
            message=f"status={debate['status']}", round=1,
            confidence_before=debate["confidence_before"],
            confidence_after=debate["confidence_after"],
        )

        if debate["status"] == "resolved":
            winner = debate["winner"]
            final_conf = debate["confidence_after"][winner]

            if final_conf <= MIN_WINNING_CONFIDENCE:
                db.insert_arbitration_log(
                    incident_id, event_type="weak_evidence",
                    message=f"{winner} won the {len(group)}-way debate with confidence "
                            f"{final_conf:.2f} <= MIN_WINNING_CONFIDENCE={MIN_WINNING_CONFIDENCE}. "
                            "Declared undecided.",
                    round=1,
                )
                for hid in hyp_ids.values():
                    db.update_hypothesis_status(hid, "undecided", rejection_reason="Weak evidence after debate")
                db.close_incident(incident_id, status="undecided")
            else:
                eliminated_name = (debate.get("eliminated") or {}).get("agent")
                for name, hid in hyp_ids.items():
                    if name == winner:
                        db.update_hypothesis_status(hid, "winner")
                    elif name == eliminated_name:
                        db.update_hypothesis_status(
                            hid, "rejected",
                            rejection_reason="Eliminated as weakest in the first three-way challenge round",
                        )
                    elif name in group:
                        db.update_hypothesis_status(hid, "rejected", rejection_reason="Lost debate round")
                    else:
                        db.update_hypothesis_status(hid, "rejected", rejection_reason="Not top candidate")
                db.close_incident(incident_id, status="resolved",
                                   winner_agent=winner, final_confidence=final_conf)
                approvals.propose_action(incident_id, winner, results[winner]["hypothesis"], final_conf)

        else:
            eliminated_name = (debate.get("eliminated") or {}).get("agent")
            for name, hid in hyp_ids.items():
                if name == eliminated_name:
                    db.update_hypothesis_status(
                        hid, "rejected",
                        rejection_reason="Eliminated as weakest in the first three-way challenge round",
                    )
                elif name in group:
                    db.update_hypothesis_status(hid, "undecided", rejection_reason="Still tied after debate")
                else:
                    db.update_hypothesis_status(hid, "rejected", rejection_reason="Not top candidate")
            db.close_incident(incident_id, status="undecided")

    elif decision["leader"] is None:
        db.insert_arbitration_log(incident_id, event_type="all_failed",
                                  message="All agents failed; nothing to arbitrate.", round=1)
        db.close_incident(incident_id, status="undecided")
    else:
        winner = decision["leader"]
        final_conf = results[winner]["confidence"]

        if final_conf <= MIN_WINNING_CONFIDENCE:
            db.insert_arbitration_log(
                incident_id, event_type="weak_evidence",
                message=f"{winner} led with confidence {final_conf:.2f} < "
                        f"MIN_WINNING_CONFIDENCE={MIN_WINNING_CONFIDENCE}. Declared undecided.",
                round=1,
            )
            for hid in hyp_ids.values():
                db.update_hypothesis_status(hid, "undecided", rejection_reason="Weak evidence")
            db.close_incident(incident_id, status="undecided")
        else:
            for name, hid in hyp_ids.items():
                status = "winner" if name == winner else "rejected"
                reason = None if status == "winner" else "Lower confidence, no tie"
                db.update_hypothesis_status(hid, status, rejection_reason=reason)
            db.close_incident(incident_id, status="resolved",
                               winner_agent=winner, final_confidence=final_conf)
            approvals.propose_action(incident_id, winner, results[winner]["hypothesis"], final_conf)

    experience.clear_replay_challenges()
    if lesson is None:
        # تحقيق كامل → درس جديد (أو تحديث الدرس القديم لو الأدلة تغيّرت)
        try:
            experience.record(event_text, region, incident_id, results, debate, _time.time() - t_start)
        except Exception:  # noqa: BLE001
            pass

    # وكيل المخاطر الجغرافية والأثر (Geospatial Risk & Impact Agent): يشتغل دايماً
    # آخر شي، بعد ما الحادثة تنقفل نهائياً (resolved أو undecided) — تحليل بعدي،
    # ما يدخل بمنافسة الفرضيات ولا بجولة النقاش. أي فشل فيه ما لازم يكسر التدفق
    # الرئيسي (الحادثة أصلاً انحفظت وانقفلت بنجاح قبل ما نوصله)، فمحاط بـ try/except.
    geo_assessment = None
    try:
        geo_assessment = run_geo_assessment(incident_id, emit=traced_emit)
    except Exception as e:
        traced_emit({"type": "geo_error", "incident_id": incident_id, "error": str(e)})

    return {"incident_id": incident_id, "results": results, "geo": geo_assessment,
            "decision": decision, "debate": debate}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--save":
        # python agents/arbitrator.py --save "نص الحدث"
        event = sys.argv[2] if len(sys.argv) > 2 else (
            "Meter MAC000689 in Madaba region rose from 22.63 to 33.97 kWh/day "
            "as of 2020-02-14."
        )
        out = run_and_save(event, region=None, emit=print)
        print("\n=== SAVED ===")
        print(f"incident_id: {out['incident_id']}")
        print(f"decision: {out['decision']['status']}")
        if out["debate"]:
            print(f"debate: {out['debate']['status']}, winner={out['debate']['winner']}")
    else:
        # python agents/arbitrator.py
        event = sys.argv[1] if len(sys.argv) > 1 else (
            "Meter MAC000689 in Madaba region rose from 22.63 to 33.97 kWh/day "
            "as of 2020-02-14."
        )
        out = dispatch(event, emit=print)
        decision = compare_confidence(out)

        print("\n=== SUMMARY ===")
        for name, r in out.items():
            print(f"{name}: confidence={r['confidence']:.2f} -> {r['hypothesis'][:120]}")

        print("\n=== DECISION ===")
        print(f"status: {decision['status']}")
        if decision["status"] == "tie":
            group = decision["tied_group"]
            print(f"TIE between {len(group)} agents: "
                  + ", ".join(f"{n} ({out[n]['confidence']:.2f})" for n in group)
                  + f" -> gap={decision['gap']} < TIE_THRESHOLD={config.TIE_THRESHOLD}. "
                    "Starting debate round.")
            debate = debate_round(out, decision, event, emit=print)
            print("\n=== DEBATE RESULT ===")
            print(f"status: {debate['status']}")
            for name in debate["group"]:
                print(f"{name}: {debate['confidence_before'][name]:.2f} -> "
                      f"{debate['confidence_after'][name]:.2f}")
            for ch in debate["challenges"]:
                print(f"  {ch['by']} challenges {ch['against']}: "
                      f"{ch['challenged_tools']} ({ch['reason']})")
            if debate["status"] == "resolved":
                print(f"WINNER after debate: {debate['winner']}")
            else:
                print(f"Still tied after debate (gap={debate['gap_after']}) -> inconclusive (4.5).")
        else:
            print(f"CLEAR winner: {decision['leader']} ({out[decision['leader']]['confidence']:.2f})")