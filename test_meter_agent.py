import sys
from agents.meter_agent import create_meter_agent

meter = sys.argv[1] if len(sys.argv) > 1 else "MAC000379"

agent = create_meter_agent()
result = agent.run(
    f"Incident: meter {meter} shows unusual consumption. Investigate."
)

print("\n===== FINAL =====")
print("Meter:", meter)
print("Confidence:", result["confidence"])
print(result["hypothesis"])