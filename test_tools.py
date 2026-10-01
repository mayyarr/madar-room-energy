import json
from db.database import get_conn
from tools.meter_tools import (
    get_meter_summary, detect_consumption_drop, compare_neighbor_meters
)

conn = get_conn()
ids = [r["meter_id"] for r in conn.execute("SELECT meter_id FROM meters LIMIT 5")]
conn.close()

for mid in ids:
    print("=====", mid)
    for fn in (get_meter_summary, detect_consumption_drop, compare_neighbor_meters):
        print(json.dumps(fn(mid), indent=2))
        