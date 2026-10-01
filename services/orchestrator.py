"""
services/orchestrator.py
=========================
الحلقة اللي بتوصل الوكيل بقاعدة البيانات: تفتح الحدث، تشغل وكيل (أو أكثر)
عليه، وتحفظ كل خطوة أول بأول (مو بس النتيجة النهائية) — تشغيلة الوكيل،
كل استدعاء أداة خام، كل حدث بث (emit)، الفرضية، والأدلة.

هاد الملف ما بيعرف شي عن التحكيم (مين فاز، جولة النقاش) — هاد شغل
agents/arbitrator.py (P4). دور orchestrator بس إنه "يشغل ويحفظ بأمانة".
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import database as db


def run_agent_on_incident(agent, incident_id, event_text, round=1, emit=print):
    """
    يشغل وكيلاً واحداً على حدث موجود أصلاً بقاعدة البيانات، ويحفظ كل شيء:
    - agent_runs: سجل تشغيلة (running -> success/error)
    - tool_calls: كل استدعاء أداة خام (input + output مع بعض)
    - trace_events: نسخة من كل حدث بث (tool_call, tool_result, hypothesis)
    - hypotheses + evidence: الفرضية النهائية وأدلتها (حسب العقد)

    يرجع: dict النتيجة الأصلي من agent.run() + "hypothesis_id" و"agent_run_id".
    """
    run_id = db.start_agent_run(incident_id, agent.name, round=round)

    # نخزن آخر tool_call لكل أداة مؤقتاً حتى يوصل الـ tool_result المقابل له
    # (emit بيبعتهم بحدثين منفصلين: tool_call فيه args، tool_result فيه النتيجة)
    pending_calls = []

    def wrapped_emit(event):
        emit(event)  # نحافظ على السلوك الأصلي (طباعة أو بث حي لاحقاً)

        db.insert_trace_event(
            incident_id, event.get("agent", agent.name),
            event.get("type", "unknown"), event,
        )

        etype = event.get("type")
        if etype == "tool_call":
            pending_calls.append({
                "tool": event.get("tool"),
                "args": event.get("args"),
                "forced": event.get("forced", False),
            })
        elif etype == "tool_result":
            # نلاقي أقرب tool_call بنفس الاسم لسا ما انربط بنتيجة
            tool_name = event.get("tool")
            match = next((c for c in reversed(pending_calls)
                          if c["tool"] == tool_name and "matched" not in c), None)
            if match is not None:
                match["matched"] = True
                db.insert_tool_call(
                    run_id, tool_name, match["args"], event.get("result"),
                    forced=match["forced"],
                )
            else:
                # احتياط: نتيجة بدون استدعاء مسجّل (ما لازم يصير حسب العقد)
                db.insert_tool_call(run_id, tool_name, None, event.get("result"))

    try:
        result = agent.run(event_text, emit=wrapped_emit)
    except Exception as e:
        db.finish_agent_run(run_id, "error", error_message=str(e))
        raise

    hypothesis_id = db.save_agent_result(incident_id, run_id, result, round=round)

    result = dict(result)
    result["hypothesis_id"] = hypothesis_id
    result["agent_run_id"] = run_id
    return result


def run_agents_on_incident(agents, incident_id, event_text, round=1, emit=print):
    """
    يشغل عدة وكلاء بالتتابع على نفس الحدث (كل وحدة تحفظ نفسها بشكل مستقل).
    هاد اللي بيستخدمه agents/arbitrator.py لاحقاً (P4) قبل ما يقارن الفرضيات.
    يرجع لستة نتائج، وحدة لكل وكيل، بنفس ترتيب agents.
    """
    return [run_agent_on_incident(agent, incident_id, event_text, round=round, emit=emit)
            for agent in agents]


def open_incident(title, description="", severity=None, region=None, event_time=None):
    """يفتح حدثاً جديداً بقاعدة البيانات ويرجع incident_id. غلاف رقيق حول database.py
    حتى تبلش arbitrator.py منه بدون ما تستورد db مباشرة."""
    return db.insert_incident(
        title, description=description, severity=severity,
        region=region, event_time=event_time,
    )


if __name__ == "__main__":
    # تجربة يدوية سريعة: تشغيل Meter Agent فعلياً على MAC002068 وحفظ كل شيء.
    # للتشغيل: python services\orchestrator.py  (من داخل venv، فيها GROQ_API_KEY)
    from agents.meter_agent import create_meter_agent

    db.init_db()

    incident_id = open_incident(
        title="هبوط استهلاك مفاجئ - MAC002068",
        description="عداد MAC002068 (Amman) هبط استهلاكه اليومي بشكل حاد",
        region="Amman",
    )
    print(f"Incident #{incident_id} opened.")

    agent = create_meter_agent()
    event_text = ("Meter MAC002068 in Amman shows a sudden drop in daily consumption "
                  "compared to its recent history and its neighbors. Investigate.")

    result = run_agent_on_incident(agent, incident_id, event_text)

    print("\n--- Saved result ---")
    print("hypothesis_id:", result["hypothesis_id"])
    print("agent_run_id:", result["agent_run_id"])
    print("confidence:", result["confidence"])
    print("hypothesis:", result["hypothesis"][:200])
