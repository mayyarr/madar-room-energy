"""اختبار شامل لطبقة الحفظ: يبني حدثاً كاملاً من البداية للنهاية ويتأكد أنه يُقرأ صح."""
from db.database import (
    init_db, insert_incident, close_incident, get_incident,
    start_agent_run, save_agent_result, finish_agent_run,
    insert_trace_event, list_trace_events,
    insert_arbitration_log, list_arbitration_log,
    insert_action, request_approval, decide_approval, list_pending_approvals,
    get_conn,
)

added = init_db()
print("Migration added:", added or "لا شيء (محدثة أصلاً)")

incident_id = insert_incident(
    "ارتفاع استهلاك مفاجئ MAC000689", region="Madaba", severity="medium",
    event_time="2020-02-14",
)
print("incident_id:", incident_id)

run_id = start_agent_run(incident_id, "Meter Agent", round=1)
insert_trace_event(incident_id, "Meter Agent", "tool_call", {"tool": "get_meter_summary"})
fake_result = {
    "agent": "Meter Agent",
    "hypothesis": "ارتفاع جديد مقارنة بالجيران",
    "confidence": 0.60,
    "evidence": [
        {"tool": "compare_history_vs_neighbors", "input": {"meter_id": "MAC000689"},
         "output": {"flagged": True, "strength": 0.6}},
    ],
}
hyp_id = save_agent_result(incident_id, run_id, fake_result, round=1)
print("hypothesis_id:", hyp_id)

insert_arbitration_log(
    incident_id, "tie_detected", "الفرق بين Meter وCharging أقل من العتبة",
    challenger_agent="Charging Agent", challenged_agent="Meter Agent",
    confidence_before={"Meter Agent": 0.60, "Charging Agent": 0.67},
)

action_id = insert_action(incident_id, "notify_technician", "إرسال فني للتحقق من العداد", risk_level="low")
approval_id = request_approval(incident_id, action_id, "إرسال فني للتحقق من العداد", risk_level="low")
decide_approval(approval_id, "approved", decided_by="ميار", comment="تمت الموافقة للاختبار")

close_incident(incident_id, "resolved", winner_agent="Charging Agent", final_confidence=0.67)

print("\n--- الحدث ---")
print(get_incident(incident_id))
print("\n--- سجل التتبع ---")
print(list_trace_events(incident_id))
print("\n--- سجل التحكيم ---")
print(list_arbitration_log(incident_id))
print("\n--- الموافقات المعلّقة (لازم فاضية الآن) ---")
print(list_pending_approvals(incident_id))

conn = get_conn()
w = conn.execute("SELECT weight FROM evidence WHERE hypothesis_id = ?", (hyp_id,)).fetchone()
conn.close()
print("\n--- التحقق من weight (لازم يطلع 0.6 مش 1.0) ---")
print(dict(w))