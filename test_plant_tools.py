import json, sys
from tools import plant_tools as pt

unit = int(sys.argv[1]) if len(sys.argv) > 1 else 1
TOOLS = [pt.get_unit_summary, pt.detect_thermal_anomaly, pt.estimate_remaining_life,
         pt.compare_engine_to_fleet, pt.get_degradation_trend, pt.check_sensor_quality]

total = pt._load()[1].set_index("unit_id").loc[unit, "total_cycles"]
for label, as_of in [("EARLY", 60), ("MID", int(total * 0.7)), ("LATE", int(total) - 5)]:
    print(f"\n===== unit {unit} | {label} | as_of_cycle={as_of} (total {total}) =====")
    for f in TOOLS:
        r = f(unit, as_of)
        print(f"{r['tool']:26s} flagged={r.get('flagged')} strength={r.get('strength')}")
        print("   ", r.get("summary") or r.get("error")) 