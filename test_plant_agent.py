"""
تجربة Plant Agent:  python test_plant_agent.py [unit] [cycle]
"""
import sys

from agents.plant_agent import create_plant_agent

unit = int(sys.argv[1]) if len(sys.argv) > 1 else 1
cycle = int(sys.argv[2]) if len(sys.argv) > 2 else 187

event = (f"Incident: unit {unit} shows rising temperatures and the operators are worried. "
         f"Investigate the unit as of cycle {cycle}.")

agent = create_plant_agent()
r = agent.run(event)

print("\nPlant:", r["agent"])
print("Confidence:", r["confidence"])
print("Hypothesis:", r["hypothesis"])
print("Tools used:", [e["tool"] for e in r["evidence"]])