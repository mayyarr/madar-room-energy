from db.database import get_conn
from tools.meter_tools import detect_consumption_drop, compare_history_vs_neighbors

conn = get_conn()
ids = [r["meter_id"] for r in conn.execute("SELECT meter_id FROM meters")]
conn.close()

rows = []
for i, mid in enumerate(ids, 1):
    d = detect_consumption_drop(mid)
    h = compare_history_vs_neighbors(mid)
    if "error" in d or "error" in h:
        continue
    rows.append({
        "meter": mid,
        "drop_pct": d["drop_pct"],
        "drop_flag": d["flagged"],
        "recent_ratio": h["recent_ratio_vs_neighbors"],
        "hist_ratio": h["historical_ratio_vs_neighbors"],
        "new_anomaly": h["new_anomaly"],
    })

print("\n--- Biggest consumption drops ---")
for r in sorted(rows, key=lambda r: r["drop_pct"], reverse=True)[:8]:
    print(r)

print("\n--- New anomalies vs neighbors ---")
for r in [r for r in rows if r["new_anomaly"]]:
    print(r)

print("\nScanned", len(rows), "meters")