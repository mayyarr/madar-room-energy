"""
seed_jordan.py - يحمّل الداتا الأردنية الاصطناعية (data/jordan_synthetic/) لقاعدة madar.db

    python db/generate_jordan_synthetic.py   # مرة وحدة (أو لو بدك تعيدي توليدها)
    python db/seed_jordan.py                 # يحمّلها للقاعدة
    python db/seed_jordan.py --remove        # يشيلها من القاعدة ويرجّع كل شي متل ما كان

آمن ينعاد تشغيله: بيمسح أي داتا أردنية قديمة (معرّفات JO / وحدات 201-202) قبل ما يضيف.
ما بيلمس داتا لندن / ACN / NASA الأصلية أبداً.
"""
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db.database import get_conn  # noqa: E402

DIR = os.path.join(ROOT, "data", "jordan_synthetic")
UNITS = (201, 202)


def remove(conn):
    conn.execute("DELETE FROM meter_readings WHERE meter_id LIKE 'JOM%'")
    conn.execute("DELETE FROM meters WHERE meter_id LIKE 'JOM%'")
    conn.execute("DELETE FROM charging_sessions WHERE charger_id LIKE 'JO-%'")
    conn.execute("DELETE FROM chargers WHERE charger_id LIKE 'JO-%'")
    conn.execute(f"DELETE FROM plant_sensors WHERE unit_id IN {UNITS}")
    conn.execute(f"DELETE FROM plant_units WHERE unit_id IN {UNITS}")


def seed():
    if not os.path.exists(os.path.join(DIR, "scenarios.json")):
        raise SystemExit("ما في داتا أردنية. شغّلي أول: python db/generate_jordan_synthetic.py")
    conn = get_conn()
    remove(conn)
    for table, fname in [("meters", "meters.csv"), ("meter_readings", "meter_readings.csv"),
                         ("chargers", "chargers.csv"), ("charging_sessions", "charging_sessions.csv"),
                         ("plant_units", "plant_units.csv"), ("plant_sensors", "plant_sensors.csv")]:
        pd.read_csv(os.path.join(DIR, fname)).to_sql(table, conn, if_exists="append", index=False)
    conn.commit()
    counts = {t: conn.execute(q).fetchone()[0] for t, q in [
        ("meters", "SELECT COUNT(*) FROM meters WHERE meter_id LIKE 'JOM%'"),
        ("chargers", "SELECT COUNT(*) FROM chargers WHERE charger_id LIKE 'JO-%'"),
        ("sessions", "SELECT COUNT(*) FROM charging_sessions WHERE charger_id LIKE 'JO-%'"),
        ("plant_units", f"SELECT COUNT(*) FROM plant_units WHERE unit_id IN {UNITS}"),
    ]}
    conn.close()
    print("تم تحميل الداتا الأردنية الاصطناعية:", counts)


if __name__ == "__main__":
    if "--remove" in sys.argv:
        c = get_conn(); remove(c); c.commit(); c.close()
        print("تم حذف الداتا الأردنية الاصطناعية من القاعدة.")
    else:
        seed()
