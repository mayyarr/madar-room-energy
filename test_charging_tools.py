import sys
from tools import charging_tools as ct

region = sys.argv[1] if len(sys.argv) > 1 else "Madaba"
day = sys.argv[2] if len(sys.argv) > 2 else "2020-02-25"
meter_delta = float(sys.argv[3]) if len(sys.argv) > 3 else 11.3

calls = [
    ct.get_region_charging_summary(region, day),
    ct.detect_charging_load_shift(region, day),
    ct.detect_new_charger_activity(region, day),
    ct.compare_charger_failure_rate(region, day),
    ct.explain_meter_change(region, meter_delta, day),
    ct.check_charging_data_quality(region, day),
]
print(f"===== {region} | as_of {day} | meter_delta {meter_delta} kWh/day =====")
for r in calls:
    print(f"{r['tool']:30s} flagged={r.get('flagged')} strength={r.get('strength')}")
    print("   ", r.get("summary") or r.get("error"))