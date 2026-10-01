"""
plant_tools.py  -  أدوات وكيل المعدات (Plant Agent)
كل أداة ترجع dict فيه: tool, strength (0-1), flagged, summary
(والأداة اللي تفحص جودة الحساسات ترجع كمان refutes)
مهم: الأدوات ما تقرأ عمود rul للمحرك المفحوص (هذا "المستقبل" وما لازم نعرفه).
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db.database import get_conn  # noqa: E402

# ---------------- إعدادات (بنضبطها بعد أول تجربة) ----------------
SENSORS_UP = ["s2", "s3", "s4", "s8", "s9", "s11", "s13", "s14", "s15", "s17"]  # بتزيد مع التدهور
SENSORS_DOWN = ["s7", "s12", "s20", "s21"]                                    # بتنقص مع التدهور
KEY = SENSORS_UP + SENSORS_DOWN
THERMAL = ["s2", "s3", "s4"]   # حساسات الحرارة

BASE_N = 30          # أول 30 دورة = الحالة الطبيعية للمحرك
MIN_HISTORY = 45     # أقل عدد دورات لازم نشوفها قبل ما نحكم
WINDOW = 10          # نافذة "الآن"
THERMAL_Z = 2.5      # حد z للحرارة
LIFE_ALERT = 75      # إذا العمر المتبقي المقدّر <= هذا نعتبره خطر
SLOPE_T = 0.02       # حد ميل التدهور بالدورة

_cache = {}


def _load():
    """بيقرأ الداتا مرة وحدة وبيحسب الـ z-scores و health index لكل المحركات"""
    if "df" in _cache:
        return _cache["df"], _cache["units"]
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM plant_sensors ORDER BY unit_id, cycle", conn)
    units = pd.read_sql_query("SELECT * FROM plant_units", conn)
    conn.close()

    base = df[df["cycle"] <= BASE_N].groupby("unit_id")[KEY].agg(["mean", "std"])
    base_mean = base.xs("mean", axis=1, level=1)
    noise = base.xs("std", axis=1, level=1).mean()   # ضجيج كل حساس

    for s in KEY:
        z = (df[s] - df["unit_id"].map(base_mean[s])) / noise[s]
        df["z_" + s] = -z if s in SENSORS_DOWN else z   # موجب = اتجاه التدهور

    df["hi"] = df[["z_" + s for s in KEY]].mean(axis=1)
    df["hi_s"] = df.groupby("unit_id")["hi"].transform(
        lambda x: x.rolling(WINDOW, min_periods=1).mean()
    )
    _cache["df"], _cache["units"] = df, units
    return df, units


def _unit(unit_id, as_of_cycle=None):
    df, units = _load()
    d = df[df["unit_id"] == int(unit_id)]
    if d.empty:
        return None, None, None
    if as_of_cycle is not None:
        d = d[d["cycle"] <= int(as_of_cycle)]
    site = units.loc[units["unit_id"] == int(unit_id), "site"].iloc[0]
    return d, site, df


def _err(tool, msg):
    return {"tool": tool, "error": msg}


def _short(tool, d):
    """لو الداتا قليلة ما نحكم"""
    return {
        "tool": tool, "strength": 0.0, "flagged": False,
        "summary": f"Only {len(d)} cycles observed; need {MIN_HISTORY} to judge.",
    }


# ---------------------------- الأدوات ----------------------------

def get_unit_summary(unit_id, as_of_cycle=None):
    tool = "get_unit_summary"
    d, site, _ = _unit(unit_id, as_of_cycle)
    if d is None:
        return _err(tool, f"unit {unit_id} not found")
    return {
        "tool": tool, "unit_id": int(unit_id), "site": site,
        "cycles_observed": int(len(d)),
        "health_index_now": round(float(d["hi_s"].iloc[-1]), 2),
        "strength": 0.0, "flagged": False,
        "summary": f"Unit {int(unit_id)} at {site}, {len(d)} cycles observed, "
                   f"health index {float(d['hi_s'].iloc[-1]):.2f} (0 = like new, higher = more worn).",
    }


def detect_thermal_anomaly(unit_id, as_of_cycle=None):
    """هل حرارة المحرك الحالية أعلى من حالته الطبيعية؟"""
    tool = "detect_thermal_anomaly"
    d, site, _ = _unit(unit_id, as_of_cycle)
    if d is None:
        return _err(tool, f"unit {unit_id} not found")
    if len(d) < MIN_HISTORY:
        return _short(tool, d)
    zs = {s: float(d["z_" + s].tail(WINDOW).mean()) for s in THERMAL}
    n_high = sum(1 for v in zs.values() if v >= THERMAL_Z)
    mean_z = float(np.mean(list(zs.values())))
    flagged = bool(n_high >= 2)
    strength = float(np.clip((mean_z - 1.5) / 4.0, 0, 1)) if flagged else 0.0
    return {
        "tool": tool, "z_scores": {k: round(v, 2) for k, v in zs.items()},
        "sensors_above_threshold": int(n_high),
        "strength": round(strength, 2), "flagged": flagged,
        "summary": (f"Thermal sensors (s2,s3,s4) z vs own baseline: "
                    + ", ".join(f"{k}={v:.1f}" for k, v in zs.items())
                    + (". Abnormally hot." if flagged else ". Within normal range.")),
    }


def estimate_remaining_life(unit_id, as_of_cycle=None):
    """يقدّر العمر المتبقي بمقارنة حالة المحرك بمحركات ثانية وصلت لنفس الحالة"""
    tool = "estimate_remaining_life"
    d, site, df = _unit(unit_id, as_of_cycle)
    if d is None:
        return _err(tool, f"unit {unit_id} not found")
    if len(d) < MIN_HISTORY:
        return _short(tool, d)
    hi_now = float(d["hi_s"].iloc[-1])
    others = df[df["unit_id"] != int(unit_id)]
    tol = 0.25
    sel = others[(others["hi_s"] - hi_now).abs() <= tol]
    while len(sel) < 40 and tol < 3:
        tol *= 1.5
        sel = others[(others["hi_s"] - hi_now).abs() <= tol]
    est = float(sel["rul"].median())
    p25, p75 = float(sel["rul"].quantile(0.25)), float(sel["rul"].quantile(0.75))
    flagged = bool(est <= LIFE_ALERT)
    strength = float(np.clip((100 - est) / 80.0, 0, 1)) if flagged else 0.0
    return {
        "tool": tool, "health_index_now": round(hi_now, 2),
        "estimated_rul_cycles": round(est), "rul_range_cycles": [round(p25), round(p75)],
        "strength": round(strength, 2), "flagged": flagged,
        "summary": (f"Engines in a similar condition had a median of {est:.0f} cycles left "
                    f"(typical {p25:.0f}-{p75:.0f})."),
    }


def compare_engine_to_fleet(unit_id, as_of_cycle=None):
    """هل المحرك بيتدهور أسرع من باقي المحركات بنفس العمر؟"""
    tool = "compare_engine_to_fleet"
    d, site, df = _unit(unit_id, as_of_cycle)
    if d is None:
        return _err(tool, f"unit {unit_id} not found")
    if len(d) < MIN_HISTORY:
        return _short(tool, d)
    age = int(d["cycle"].iloc[-1])
    peers = df[(df["unit_id"] != int(unit_id)) & ((df["cycle"] - age).abs() <= 3)]
    if len(peers) < 10:
        return _err(tool, "not enough peer engines at this age")
    hi_now = float(d["hi_s"].iloc[-1])
    mu, sd = float(peers["hi_s"].mean()), float(peers["hi_s"].std())
    z = (hi_now - mu) / sd if sd > 0 else 0.0
    flagged = bool(z >= 2.0)
    strength = float(np.clip((z - 1.0) / 3.0, 0, 1)) if flagged else 0.0
    return {
        "tool": tool, "age_cycles": age, "health_index_now": round(hi_now, 2),
        "peer_mean_health_index": round(mu, 2), "z_vs_peers": round(float(z), 2),
        "strength": round(strength, 2), "flagged": flagged,
        "summary": (f"At {age} cycles this engine's wear is {hi_now:.2f} vs peer average {mu:.2f} "
                    f"(z={z:.1f}). " + ("Wearing faster than peers." if flagged else "In line with peers.")),
    }


def get_degradation_trend(unit_id, as_of_cycle=None):
    """هل التدهور بيسرّع؟ (ميل الـ health index بآخر 30 دورة)"""
    tool = "get_degradation_trend"
    d, site, _ = _unit(unit_id, as_of_cycle)
    if d is None:
        return _err(tool, f"unit {unit_id} not found")
    if len(d) < MIN_HISTORY:
        return _short(tool, d)
    hi = d["hi_s"].to_numpy()
    recent, prev = hi[-30:], hi[-60:-30]
    slope = float(np.polyfit(np.arange(len(recent)), recent, 1)[0])
    prev_slope = float(np.polyfit(np.arange(len(prev)), prev, 1)[0]) if len(prev) >= 15 else 0.0
    accelerating = bool(slope >= SLOPE_T / 2 and slope > 1.5 * max(prev_slope, 0.005))
    flagged = bool(slope >= SLOPE_T)
    strength = float(np.clip(slope / 0.06, 0, 1)) if flagged else 0.0
    return {
        "tool": tool, "slope_per_cycle": round(slope, 4), "previous_slope": round(prev_slope, 4),
        "accelerating": accelerating,
        "strength": round(strength, 2), "flagged": flagged,
        "summary": (f"Wear rising {slope:.3f}/cycle over the last 30 cycles (before that {prev_slope:.3f}). "
                    + ("Degradation is accelerating." if accelerating else "No acceleration.")),
    }


def check_sensor_quality(unit_id, as_of_cycle=None):
    """هل المشكلة من حساس خربان مش من المحرك؟ إذا إي: refutes"""
    tool = "check_sensor_quality"
    d, site, _ = _unit(unit_id, as_of_cycle)
    if d is None:
        return _err(tool, f"unit {unit_id} not found")
    if len(d) < MIN_HISTORY:
        return _short(tool, d)
    tail = d.tail(WINDOW)
    stuck = [s for s in KEY if float(tail[s].std()) == 0.0]
    missing = [s for s in KEY if int(tail[s].isna().sum()) > 0]
    zr = {s: float(d["z_" + s].tail(5).mean()) for s in KEY}
    med = float(np.median([abs(v) for v in zr.values()]))
    jumps = [s for s, v in zr.items() if abs(v) >= 5 and med < 1.0]  # حساس واحد شاذ والباقي طبيعي
    bad = sorted(set(stuck + missing + jumps))
    flagged = bool(bad)
    return {
        "tool": tool, "bad_sensors": bad, "stuck": stuck, "missing": missing, "isolated_jumps": jumps,
        "strength": 0.8 if flagged else 0.0, "flagged": flagged,
        "refutes": flagged,   # لو الحساس هو المشكلة، فتهمة "تدهور المحرك" بتضعف
        "summary": (f"Sensor problem in {bad}: likely instrument fault, not engine wear."
                    if flagged else "All key sensors look healthy (no stuck, missing, or isolated jumps)."),
    }