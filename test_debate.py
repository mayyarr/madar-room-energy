"""اختبار مواجهة ثلاثية حقيقية: الثلاثة متقاربين بالثقة."""
from agents.arbitrator import compare_confidence, debate_round

meter_result = {
    "agent": "Meter Agent", "confidence": 0.62,
    "hypothesis": "Consumption newly abnormal vs neighbors.",
    "evidence": [{"tool": "compare_history_vs_neighbors", "input": {},
                  "output": {"flagged": True, "strength": 0.6, "summary": "2.36x vs 1.16x historically"}}],
}
charging_result = {
    "agent": "Charging Agent", "confidence": 0.67,
    "hypothesis": "Charger size match with meter rise.",
    "evidence": [{"tool": "explain_meter_change", "input": {},
                  "output": {"flagged": True, "strength": 0.86, "summary": "charger matches meter change 105%"}}],
}
plant_result = {
    "agent": "Plant Agent", "confidence": 0.60,
    "hypothesis": "Engine wear accelerating near this facility's timeframe.",
    "evidence": [{"tool": "get_degradation_trend", "input": {},
                  "output": {"flagged": True, "strength": 0.6, "summary": "wear rising 0.03/cycle"}}],
}

results = {"Meter Agent": meter_result, "Charging Agent": charging_result, "Plant Agent": plant_result}
decision = compare_confidence(results)
print("decision:", decision["status"], "| tied_group:", decision["tied_group"], "| gap:", decision["gap"])

if decision["status"] == "tie":
    debate = debate_round(results, decision, "Ambiguous incident, all three plausible.", emit=print)
    print("\n=== DEBATE ===")
    print("status:", debate["status"], "| winner:", debate["winner"])
    for name in debate["group"]:
        print(name, ":", debate["confidence_before"][name], "->", debate["confidence_after"][name])