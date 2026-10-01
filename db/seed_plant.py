"""
seed_plant.py  -  يحمّل داتا NASA C-MAPSS (FD001) في madar.db
تشغيله:  python db/seed_plant.py
"""
import os
import sys

import pandas as pd

# نخلي بايثون يشوف مجلد المشروع الرئيسي
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db.database import get_conn  # noqa: E402

DATA_FILE = os.path.join(ROOT, "data", "plant", "train_FD001.txt")

# مواقع أردنية (توطين معلن: الداتا من NASA لكن منعيّنها لمواقع أردنية)
SITES = [
    "محطة العقبة الحرارية",
    "محطة الحسين الحرارية - الزرقاء",
    "محطة السمرا - الزرقاء",
    "محطة القطرانة",
]

N_UNITS = 100  # كل محركات FD001

SENSOR_COLS = [f"s{i}" for i in range(1, 22)]
COLUMNS = ["unit_id", "cycle", "op1", "op2", "op3"] + SENSOR_COLS


def create_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plant_units (
            unit_id      INTEGER PRIMARY KEY,
            site         TEXT NOT NULL,
            total_cycles INTEGER NOT NULL
        )
    """)
    sensor_defs = ", ".join(f"{c} REAL" for c in SENSOR_COLS)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS plant_sensors (
            unit_id INTEGER NOT NULL,
            cycle   INTEGER NOT NULL,
            op1 REAL, op2 REAL, op3 REAL,
            {sensor_defs},
            rul     INTEGER,
            PRIMARY KEY (unit_id, cycle)
        )
    """)
    conn.commit()


def load_data():
    df = pd.read_csv(DATA_FILE, sep=r"\s+", header=None)
    df = df.iloc[:, :26]          # أحياناً بيجي عمود فاضي بالآخر
    df.columns = COLUMNS
    # RUL = آخر دورة للمحرك - الدورة الحالية
    df["rul"] = df.groupby("unit_id")["cycle"].transform("max") - df["cycle"]
    return df


def seed():
    if not os.path.exists(DATA_FILE):
        print("ما لقيت الملف:", DATA_FILE)
        print("تأكدي إنه train_FD001.txt جوا data/plant/")
        return

    df = load_data()
    units = sorted(df["unit_id"].unique())[:N_UNITS]
    df = df[df["unit_id"].isin(units)]

    unit_rows = []
    for i, u in enumerate(units):
        total = int(df[df["unit_id"] == u]["cycle"].max())
        unit_rows.append((int(u), SITES[i % len(SITES)], total))

    conn = get_conn()
    create_tables(conn)
    conn.execute("DELETE FROM plant_sensors")
    conn.execute("DELETE FROM plant_units")
    conn.executemany(
        "INSERT INTO plant_units (unit_id, site, total_cycles) VALUES (?,?,?)",
        unit_rows,
    )
    df.to_sql("plant_sensors", conn, if_exists="append", index=False)
    conn.commit()

    n_units = conn.execute("SELECT COUNT(*) FROM plant_units").fetchone()[0]
    n_rows = conn.execute("SELECT COUNT(*) FROM plant_sensors").fetchone()[0]
    conn.close()
    print(f"تم: {n_units} محرك، {n_rows} صف قراءات")


if __name__ == "__main__":
    seed()