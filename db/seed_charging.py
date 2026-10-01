"""
seed_charging.py  -  يحمّل جلسات شحن ACN-Data (JPL) في madar.db
تشغيله من المجلد الرئيسي:  python db/seed_charging.py
"""
import json
import os
import sys
from collections import defaultdict
from datetime import timedelta
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db.database import get_conn  # noqa: E402

DATA_FILE = os.path.join(ROOT, "data", "charging", "acndata_sessions.json")

# نفس المناطق اللي وزّعنا عليها العدادات، هيك بيصير في ربط بين الشارجر والعداد
from db.regions import BASE_REGIONS as REGIONS, spread  # noqa: E402

LA_OFFSET = timedelta(hours=8)   # تقريب: توقيت كاليفورنيا الشتوي (UTC-8) لحساب "اليوم"


def create_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chargers (
            charger_id TEXT PRIMARY KEY,
            region     TEXT NOT NULL,
            space_id   TEXT,
            source     TEXT NOT NULL DEFAULT 'ACN-Data JPL (localized)'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS charging_sessions (
            session_id       TEXT PRIMARY KEY,
            charger_id       TEXT NOT NULL,
            connection_time  TEXT NOT NULL,
            disconnect_time  TEXT,
            done_charging    TEXT,
            day              TEXT NOT NULL,
            kwh_delivered    REAL,
            kwh_requested    REAL,
            minutes_available REAL,
            duration_min     REAL,
            charge_min       REAL,
            FOREIGN KEY (charger_id) REFERENCES chargers(charger_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cs_charger_day ON charging_sessions(charger_id, day)")
    conn.commit()


def to_dt(s):
    """'Fri, 01 Nov 2019 17:55:37 GMT' -> datetime (UTC)"""
    return parsedate_to_datetime(s) if s else None


def fmt(d):
    return d.strftime("%Y-%m-%d %H:%M:%S") if d else None


def minutes(a, b):
    return round((b - a).total_seconds() / 60, 1) if a and b else None


def load_sessions():
    with open(DATA_FILE, encoding="utf-8") as f:
        items = json.load(f)["_items"]

    rows, spaces = [], {}
    for it in items:
        if it.get("kWhDelivered") is None or not it.get("connectionTime"):
            continue
        conn_t = to_dt(it["connectionTime"])
        disc_t = to_dt(it.get("disconnectTime"))
        done_t = to_dt(it.get("doneChargingTime"))
        ui = (it.get("userInputs") or [{}])[-1] or {}
        station = it["stationID"]
        spaces[station] = it.get("spaceID")
        rows.append((
            it["sessionID"], station, fmt(conn_t), fmt(disc_t), fmt(done_t),
            (conn_t - LA_OFFSET).strftime("%Y-%m-%d"),
            float(it["kWhDelivered"]), ui.get("kWhRequested"), ui.get("minutesAvailable"),
            minutes(conn_t, disc_t), minutes(conn_t, done_t),
        ))
    return rows, spaces


def seed():
    if not os.path.exists(DATA_FILE):
        print("ما لقيت الملف:", DATA_FILE)
        return
    rows, spaces = load_sessions()
    stations = sorted(spaces)
        # الإزاحة 4: بتنقل شواحن الحدث الحقيقي (فبراير 2020) من Zarqa إلى Madaba، لتتطابق مع عداد
    # سيناريو التعادل MAC000689. التوزيع على المناطق كله تعيين توضيحي (توطين) ويُذكر بالواجهة.
    reg = spread([(s, REGIONS[(i + 4) % len(REGIONS)]) for i, s in enumerate(stations)])
    chargers = [(s, reg[s], spaces[s]) for s in stations]

    conn = get_conn()
    create_tables(conn)
    conn.execute("DELETE FROM charging_sessions")
    conn.execute("DELETE FROM chargers")
    conn.executemany("INSERT INTO chargers (charger_id, region, space_id) VALUES (?,?,?)", chargers)
    conn.executemany("INSERT OR REPLACE INTO charging_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()

    n_ch = conn.execute("SELECT COUNT(*) FROM chargers").fetchone()[0]
    n_se = conn.execute("SELECT COUNT(*) FROM charging_sessions").fetchone()[0]
    span = conn.execute("SELECT MIN(day), MAX(day) FROM charging_sessions").fetchone()
    per_region = conn.execute("SELECT region, COUNT(*) FROM chargers GROUP BY region").fetchall()
    conn.close()
    print(f"تم: {n_ch} شاحن، {n_se} جلسة، من {span[0]} إلى {span[1]}")
    print("الشواحن لكل منطقة:", {r[0]: r[1] for r in per_region})


if __name__ == "__main__":
    seed()