"""
تجربة Charging Agent:  python test_charging_agent.py [region] [date] [meter_delta]
"""
import sys

from agents.charging_agent import create_charging_agent

region = sys.argv[1] if len(sys.argv) > 1 else "Madaba"
day = sys.argv[2] if len(sys.argv) > 2 else "2020-02-14"
delta = sys.argv[3] if len(sys.argv) > 3 else "11.3"

event = (f"Incident: a smart meter in {region} shows its daily consumption rising by {delta} kWh/day "
         f"compared with its own history. Simulated incident date: {day}. "
         f"Investigate whether EV charging in {region} explains it.")

agent = create_charging_agent()
r = agent.run(event)

print("\nCharging:", r["agent"])
print("Confidence:", r["confidence"])
print("Hypothesis:", r["hypothesis"])
print("Tools used:", [e["tool"] for e in r["evidence"]])