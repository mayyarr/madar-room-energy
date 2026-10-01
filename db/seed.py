import glob
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)
from db.database import get_conn, init_db

# مناطق أردنية نوزّع عليها العدادات.
# ملاحظة: التوزيع اصطناعي (توطين)، لأن داتا لندن لا تحتوي مناطق أردنية.
from db.regions import BASE_REGIONS as REGIONS, spread  # noqa: E402


def load_london_blocks():
    """
    المحوّل الوحيد الذي يعرف شكل ملفات لندن.
    عند توفر داتا أردنية نغيّر هذه الدالة فقط.
    يرجع جدولاً بالأعمدة الثابتة التي تعتمدها القاعدة.
    """
    files = sorted(glob.glob(os.path.join(ROOT, "data", "meter", "block_*.csv")))
    if not files:
        raise SystemExit("No block_*.csv files found in data/")
    print("Reading:", [os.path.basename(f) for f in files])

    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df = df.rename(columns={
        "LCLid": "meter_id",
        "energy_sum": "kwh_total",
        "energy_mean": "kwh_mean",
        "energy_max": "kwh_max",
        "energy_min": "kwh_min",
        "energy_std": "kwh_std",
        "energy_count": "readings_count",
    })
    df = df[["meter_id", "day", "kwh_total", "kwh_mean", "kwh_max",
             "kwh_min", "kwh_std", "readings_count"]]
    df = df.drop_duplicates(subset=["meter_id", "day"]).dropna(subset=["kwh_total"])
    return df


def seed():
    init_db()
    df = load_london_blocks()

    meter_ids = sorted(df["meter_id"].unique())
    meters = pd.DataFrame({
        "meter_id": meter_ids,
        "region": list(spread([(m, REGIONS[i % len(REGIONS)]) for i, m in enumerate(meter_ids)]).values()),
    })

    conn = get_conn()
    conn.execute("DELETE FROM meter_readings")
    conn.execute("DELETE FROM meters")
    meters.to_sql("meters", conn, if_exists="append", index=False)
    df.to_sql("meter_readings", conn, if_exists="append", index=False)
    conn.commit()

    n_m = conn.execute("SELECT COUNT(*) FROM meters").fetchone()[0]
    n_r = conn.execute("SELECT COUNT(*) FROM meter_readings").fetchone()[0]
    conn.close()
    print(f"Loaded {n_m} meters and {n_r} readings.")


if __name__ == "__main__":
    seed()