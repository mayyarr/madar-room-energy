"""
generate_jordan_synthetic.py - يولّد داتا أردنية اصطناعية فيها أعطال محقونة سببها معروف مسبقاً.

الهدف: نختبر الوكلاء على حوادث بنعرف جوابها الصح من الأصل (ground truth)، عشان نتأكد
إنه النتائج يلي بيطلعها النظام صحيحة فعلاً — مش بس "بتبين منطقية".

يكتب ملفات CSV جوا data/jordan_synthetic/ + scenarios.json (مفتاح الإجابات).
التحميل للقاعدة بيصير بـ db/seed_jordan.py.

    python db/generate_jordan_synthetic.py

كل الأرقام بتتولد من بذرة ثابتة (SEED) فالنتيجة نفسها بكل مرة.
المعرّفات كلها بتبلش بـ JO عشان تتميّز عن داتا لندن/ACN/NASA:
  عدادات:  JOM11xxxx (عمّان) JOM12xxxx (إربد) JOM13xxxx (الزرقاء) JOM14xxxx (السلط)
  شواحن:   JO-SLT-CHxx (السلط) JO-KRK-CHxx (الكرك)
  وحدات:   201, 202
"""
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db.database import get_conn  # noqa: E402

OUT = os.path.join(ROOT, "data", "jordan_synthetic")
SEED = 2025
START, END = "2025-01-01", "2025-06-15"      # يوم الحادثة = آخر يوم
INCIDENT_DAY = END
rng = np.random.default_rng(SEED)
DAYS = pd.date_range(START, END, freq="D")
N = len(DAYS)


# ============================ العدادات ============================

def household_profile(base):
    """استهلاك منزلي أردني يومي: موسم تكييف يرتفع مع الصيف + ذروة الجمعة/السبت + ضجيج."""
    t = np.arange(N)
    doy = DAYS.dayofyear.to_numpy()
    winter_heat = 1.0 + 0.18 * np.clip((60 - doy) / 60, 0, 1)            # تدفئة كهربائية بكانون/شباط
    summer_ac = 1.0 + 0.45 * np.clip((doy - 120) / 50, 0, 1)             # تكييف من أيار
    weekend = np.where(DAYS.dayofweek.isin([4, 5]), 1.15, 1.0)            # الجمعة والسبت
    noise = rng.normal(1.0, 0.07, N)
    _ = t
    return base * winter_heat * summer_ac * weekend * noise


def meter_rows(meter_id, kwh, counts=None, drop_days=()):
    rows = []
    for i, d in enumerate(DAYS):
        if d.strftime("%Y-%m-%d") in drop_days:
            continue
        c = 48 if counts is None else int(counts[i])
        k = float(max(kwh[i], 0.2))
        rows.append({"meter_id": meter_id, "day": d.strftime("%Y-%m-%d"), "kwh_total": round(k, 3),
                     "kwh_mean": round(k / max(c, 1), 4), "kwh_max": round(k / max(c, 1) * 3.2, 4),
                     "kwh_min": round(k / max(c, 1) * 0.25, 4), "kwh_std": round(k / max(c, 1) * 0.6, 4),
                     "readings_count": c})
    return rows


def build_meters():
    meters, readings = [], []

    def neighbours(prefix, region, n=10):
        for j in range(2, 2 + n):
            mid = f"{prefix}{j:04d}"
            meters.append({"meter_id": mid, "region": region})
            readings.extend(meter_rows(mid, household_profile(rng.uniform(11, 17))))

    # M1 عمّان — تلاعب/تجاوز على العداد: الاستهلاك بينزل لـ 35% فجأة آخر 14 يوم، القراءات كاملة
    neighbours("JOM11", "Amman")
    k = household_profile(15.0)
    k[-14:] *= 0.35
    meters.append({"meter_id": "JOM110001", "region": "Amman"})
    readings.extend(meter_rows("JOM110001", k))

    # M2 إربد — عطل اتصال بالعداد: آخر 30 يوم فيها 10 أيام مفقودة كلياً + 6 أيام قراءات ناقصة
    neighbours("JOM12", "Irbid")
    k = household_profile(14.0)
    counts = np.full(N, 48)
    last30 = list(range(N - 30, N))
    missing = sorted(rng.choice(last30, 10, replace=False))
    partial = sorted(rng.choice([i for i in last30 if i not in missing], 6, replace=False))
    for i in partial:
        counts[i] = int(rng.integers(14, 30))
        k[i] *= counts[i] / 48
    meters.append({"meter_id": "JOM120001", "region": "Irbid"})
    readings.extend(meter_rows("JOM120001", k, counts,
                               drop_days={DAYS[i].strftime("%Y-%m-%d") for i in missing}))

    # M3 الزرقاء — حالة ضابطة: مستهلك كبير بطبيعته (ورشة بالبيت) دايماً ~2.4× جيرانه. ما في عطل.
    neighbours("JOM13", "Zarqa")
    k = household_profile(34.0)
    meters.append({"meter_id": "JOM130001", "region": "Zarqa"})
    readings.extend(meter_rows("JOM130001", k))

    # C1 السلط (جزء العداد) — بيت ركّب شاحن سيارة كهربائية: +9.5 kWh/يوم آخر 14 يوم
    neighbours("JOM14", "Salt")
    k = household_profile(14.0)
    k[-14:] += 9.5
    meters.append({"meter_id": "JOM140001", "region": "Salt"})
    readings.extend(meter_rows("JOM140001", k))

    return pd.DataFrame(meters), pd.DataFrame(readings)


# ============================ الشحن ============================

_sid = [0]


def session(cid, day, kwh, dur_min=None, start_hour=None):
    h = start_hour if start_hour is not None else float(rng.uniform(17.5, 21.0))
    conn = pd.Timestamp(day) + pd.Timedelta(hours=h)
    dur = dur_min if dur_min is not None else float(max(40, kwh / 7.0 * 60 + rng.uniform(10, 90)))
    charge = min(dur, kwh / 7.0 * 60) if kwh > 0.5 else 1.0
    disc = conn + pd.Timedelta(minutes=dur)
    done = conn + pd.Timedelta(minutes=charge)
    _sid[0] += 1
    return {"session_id": f"{cid}-{pd.Timestamp(day):%Y%m%d}-{_sid[0]:05d}", "charger_id": cid,
            "connection_time": conn.strftime("%Y-%m-%d %H:%M:%S"),
            "disconnect_time": disc.strftime("%Y-%m-%d %H:%M:%S"),
            "done_charging": done.strftime("%Y-%m-%d %H:%M:%S"),
            "day": pd.Timestamp(day).strftime("%Y-%m-%d"), "kwh_delivered": round(float(kwh), 3),
            "kwh_requested": round(float(kwh) * 1.1, 1), "minutes_available": round(dur + 30, 0),
            "duration_min": round(dur, 1), "charge_min": round(charge, 1)}


def build_charging():
    chargers, sessions = [], []

    def regular(cid, region, kwh_mean, per_day=1.0):
        chargers.append({"charger_id": cid, "region": region, "space_id": cid[-4:],
                         "source": "Jordan synthetic (Madar test data)"})
        for d in DAYS:
            n = rng.poisson(per_day)
            for s in range(n):
                sessions.append(session(cid, d, max(1.5, rng.normal(kwh_mean, 2.0)),
                                        start_hour=float(rng.uniform(8, 21)) + s * 0.01))

    # C1 السلط — شاحن منزلي جديد: شبه ساكت 56 يوم (جلستين صغار بس)، بعدين بيشتغل كل يوم
    regular("JO-SLT-CH01", "Salt", 8.0)
    regular("JO-SLT-CH02", "Salt", 8.0)
    new = "JO-SLT-CH05"
    chargers.append({"charger_id": new, "region": "Salt", "space_id": "CH05",
                     "source": "Jordan synthetic (Madar test data)"})
    base_days = DAYS[-70:-14]
    for d in (base_days[10], base_days[40]):
        sessions.append(session(new, d, 1.0))
    for d in DAYS[-14:]:
        sessions.append(session(new, d, rng.normal(9.5, 0.4)))

    # C2 الكرك — شاحن عطلان: آخر 28 يوم 65% من جلساته اتصال طويل (ساعتين) وطاقة شبه صفر
    for i in range(1, 6):
        regular(f"JO-KRK-CH0{i}", "Karak", 12.0, per_day=1.2)
    bad = "JO-KRK-CH03"
    cutoff = DAYS[-28]
    kept = []
    for s in sessions:
        if s["charger_id"] == bad and pd.Timestamp(s["day"]) >= cutoff and rng.random() < 0.65:
            s = session(bad, s["day"], float(rng.uniform(0.0, 0.2)), dur_min=float(rng.uniform(90, 150)))
        kept.append(s)
    return pd.DataFrame(chargers), pd.DataFrame(kept)


# ============================ المحطات ============================

KEY_UP = ["s2", "s3", "s4", "s8", "s9", "s11", "s13", "s14", "s15", "s17"]
KEY_DOWN = ["s7", "s12", "s20", "s21"]


def build_plant():
    """
    الوحدات مبنية على مسار محرك حقيقي من NASA C-MAPSS (أول 120 دورة، مرحلة سليمة)،
    وبعدين منحقن العطل بالآخر. هيك ضجيج الحساسات واقعي وما بيغيّر معايرة باقي الأسطول.
    """
    conn = get_conn()
    src = pd.read_sql_query("SELECT * FROM plant_sensors WHERE unit_id = 11 ORDER BY cycle", conn)
    full = pd.read_sql_query("SELECT * FROM plant_sensors WHERE cycle <= 30", conn)
    conn.close()
    noise = full.groupby("unit_id")[KEY_UP + KEY_DOWN].std().mean()
    healthy = src[src["cycle"] <= 120].copy()

    units, rows = [], []

    # P1 العقبة — تدهور حراري متسارع حقيقي: آخر 40 دورة كل الحساسات بتنحرف باتجاه التدهور
    u = healthy.copy()
    u["unit_id"] = 201
    ramp = np.clip(np.arange(len(u)) - (len(u) - 40), 0, None)
    for s in KEY_UP:
        u[s] = u[s] + ramp * 0.13 * noise[s]
    for s in KEY_DOWN:
        u[s] = u[s] - ramp * 0.13 * noise[s]
    u["rul"] = None
    units.append({"unit_id": 201, "site": "محطة العقبة الحرارية", "total_cycles": int(u["cycle"].max())})
    rows.append(u)

    # P2 الزرقاء — عطل حساس حرارة واحد (s3) مش عطل محرك: قفزة معزولة آخر 8 دورات، والباقي سليم
    u = healthy.copy()
    u["unit_id"] = 202
    u.loc[u.index[-8:], "s3"] = u["s3"].iloc[-8:] + 9.0 * noise["s3"]
    u["rul"] = None
    units.append({"unit_id": 202, "site": "محطة الحسين الحرارية - الزرقاء", "total_cycles": int(u["cycle"].max())})
    rows.append(u)

    return pd.DataFrame(units), pd.concat(rows, ignore_index=True)


# ============================ مفتاح الإجابات ============================

SCENARIOS = [
    {
        "id": "M1", "domain": "meter", "region": "Amman", "title": "هبوط استهلاك - JOM110001 (عمّان)",
        "event_text": "Meter JOM110001 in Amman region: daily consumption dropped sharply in the last two weeks "
                      "while neighbouring meters stayed normal. Investigate as of 2025-06-15.",
        "injected_fault": "تلاعب/تجاوز على العداد: الاستهلاك نزل لـ 35% من طبيعته آخر 14 يوم، والقراءات كاملة (مش عطل اتصال).",
        "expected_status": "resolved", "expected_winner": "Meter Agent",
        "expected_keywords": ["tamper", "bypass", "drop", "fault"],
        "checks": [
            {"tool": "detect_consumption_drop", "args": {"meter_id": "JOM110001"}, "flagged": True},
            {"tool": "compare_history_vs_neighbors", "args": {"meter_id": "JOM110001"}, "flagged": True},
            {"tool": "check_data_quality", "args": {"meter_id": "JOM110001"}, "flagged": False},
        ],
    },
    {
        "id": "M2", "domain": "meter", "region": "Irbid", "title": "قراءات ناقصة - JOM120001 (إربد)",
        "event_text": "Meter JOM120001 in Irbid region shows irregular and missing daily readings over the last "
                      "month. Investigate as of 2025-06-15.",
        "injected_fault": "عطل اتصال/عداد: 10 أيام مفقودة بالكامل + 6 أيام قراءات ناقصة من آخر 30 يوم. الاستهلاك الفعلي طبيعي.",
        "expected_status": "resolved", "expected_winner": "Meter Agent",
        "expected_keywords": ["communication", "data gap", "missing", "meter fault"],
        "checks": [
            {"tool": "check_data_quality", "args": {"meter_id": "JOM120001"}, "flagged": True},
        ],
    },
    {
        "id": "M3", "domain": "meter", "region": "Zarqa", "title": "استهلاك عالي - JOM130001 (الزرقاء) — حالة ضابطة",
        "event_text": "Meter JOM130001 in Zarqa region consumes far more than its neighbours. "
                      "Investigate as of 2025-06-15.",
        "injected_fault": "ما في عطل (حالة ضابطة): مستهلك كبير بطبيعته، دايماً ~2.4× جيرانه من أول السنة. الجواب الصح: لا عطل / غير محسوم.",
        "expected_status": "undecided", "expected_winner": None,
        "expected_keywords": ["always", "heavy consumer", "not a fault"],
        "checks": [
            {"tool": "compare_history_vs_neighbors", "args": {"meter_id": "JOM130001"}, "flagged": False,
             "field": "chronic_high_consumer", "value": True},
        ],
    },
    {
        "id": "C1", "domain": "charging", "region": "Salt", "title": "ارتفاع استهلاك - JOM140001 (السلط)",
        "event_text": "Meter JOM140001 in Salt region rose by 9.5 kWh/day in the last two weeks as of 2025-06-15. "
                      "Investigate the cause.",
        "injected_fault": "شاحن سيارة كهربائية جديد (JO-SLT-CH05) كان شبه ساكت وبلّش يشتغل يومياً آخر 14 يوم بـ ~9.5 kWh، وهو بالضبط الزيادة على العداد.",
        "expected_status": "resolved", "expected_winner": "Charging Agent",
        "expected_keywords": ["JO-SLT-CH05", "charger", "EV"],
        "checks": [
            {"tool": "detect_new_charger_activity", "args": {"region": "Salt", "as_of_day": "2025-06-15"}, "flagged": True},
            {"tool": "explain_meter_change", "args": {"region": "Salt", "as_of_day": "2025-06-15",
                                                      "meter_delta_kwh_per_day": 9.5}, "flagged": True},
        ],
    },
    {
        "id": "C2", "domain": "charging", "region": "Karak", "title": "شكاوى شحن - محطة الكرك",
        "event_text": "Drivers in Karak region complain that public EV chargers stay connected for hours but "
                      "deliver almost no energy. Investigate as of 2025-06-15.",
        "injected_fault": "الشاحن JO-KRK-CH03 عطلان: 65% من جلساته آخر 28 يوم اتصال ساعتين بطاقة شبه صفر. باقي الشواحن سليمة.",
        "expected_status": "resolved", "expected_winner": "Charging Agent",
        "expected_keywords": ["JO-KRK-CH03", "faulty", "fail"],
        "checks": [
            {"tool": "compare_charger_failure_rate", "args": {"region": "Karak", "as_of_day": "2025-06-15"}, "flagged": True},
        ],
    },
    {
        "id": "P1", "domain": "plant", "region": "Aqaba", "title": "ارتفاع حرارة - Unit 201 (العقبة)",
        "event_text": "Incident: unit 201 at Aqaba thermal station shows rising temperatures. "
                      "Investigate the unit as of cycle 120.",
        "injected_fault": "تدهور حراري حقيقي متسارع: آخر 40 دورة كل الحساسات بتنحرف مع بعض باتجاه التدهور (مش حساس واحد).",
        "expected_status": "resolved", "expected_winner": "Plant Agent",
        "expected_keywords": ["degradation", "wear", "thermal"],
        "checks": [
            {"tool": "detect_thermal_anomaly", "args": {"unit_id": 201, "as_of_cycle": 120}, "flagged": True},
            {"tool": "get_degradation_trend", "args": {"unit_id": 201, "as_of_cycle": 120}, "flagged": True},
            {"tool": "check_sensor_quality", "args": {"unit_id": 201, "as_of_cycle": 120}, "flagged": False},
        ],
    },
    {
        "id": "P2", "domain": "plant", "region": "Zarqa", "title": "قراءة حرارة شاذة - Unit 202 (الزرقاء)",
        "event_text": "Incident: unit 202 at Al-Hussein thermal station (Zarqa) reports a sudden temperature "
                      "spike. Investigate the unit as of cycle 120.",
        "injected_fault": "عطل حساس (s3) مش عطل محرك: قفزة معزولة بحساس واحد آخر 8 دورات والباقي سليم. الجواب الصح: عطل حساس / ما في تدهور محرك.",
        "expected_status": "undecided", "expected_winner": None,
        "expected_keywords": ["sensor", "s3", "instrument"],
        "checks": [
            {"tool": "check_sensor_quality", "args": {"unit_id": 202, "as_of_cycle": 120}, "flagged": True},
            {"tool": "detect_thermal_anomaly", "args": {"unit_id": 202, "as_of_cycle": 120}, "flagged": False},
        ],
    },
]


def main():
    os.makedirs(OUT, exist_ok=True)
    meters, readings = build_meters()
    chargers, sessions = build_charging()
    units, sensors = build_plant()
    meters.to_csv(os.path.join(OUT, "meters.csv"), index=False)
    readings.to_csv(os.path.join(OUT, "meter_readings.csv"), index=False)
    chargers.to_csv(os.path.join(OUT, "chargers.csv"), index=False)
    sessions.to_csv(os.path.join(OUT, "charging_sessions.csv"), index=False)
    units.to_csv(os.path.join(OUT, "plant_units.csv"), index=False)
    sensors.to_csv(os.path.join(OUT, "plant_sensors.csv"), index=False)
    with open(os.path.join(OUT, "scenarios.json"), "w", encoding="utf-8") as f:
        json.dump(SCENARIOS, f, ensure_ascii=False, indent=2)
    print(f"meters={len(meters)} readings={len(readings)} chargers={len(chargers)} "
          f"sessions={len(sessions)} units={len(units)} sensor_rows={len(sensors)} -> {OUT}")


if __name__ == "__main__":
    main()
