"""
charging_tools.py  -  أدوات وكيل الشحن (Charging Agent)
كل أداة ترجع dict: tool, strength (0-1), flagged, summary (وأحياناً refutes)
كل الأدوات بتشتغل على مستوى المنطقة (region) وبتقبل as_of_day = تاريخ "الآن" (YYYY-MM-DD)
المقارنة: آخر 14 يوم (أسبوعين) مقابل 56 يوم قبلها (8 أسابيع)، هيك أيام الأسبوع بتتساوى.
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db.database import get_conn  # noqa: E402

RECENT = 14          # أيام "الآن"
BASE = 56            # أيام المقارنة قبلها
LOAD_RATIO = 0.25    # تغيّر 25% أو أكثر بالحمل
LOAD_Z = 2.5         # وأكثر من 2.5 انحراف معياري
CHARGER_Z = 3.0      # زيادة الشاحن لازم تكون ملحوظة بالنسبة لتذبذبه هو
FAIL_KWH = 0.5       # جلسة "فاشلة" = أقل من هالطاقة رغم اتصال طويل
FAIL_MIN = 30        # ... واتصال 30 دقيقة أو أكثر
FAIL_WINDOW = 28     # نافذة نسبة الفشل
# أعياد أمريكية: الداتا من كاليفورنيا، فغياب الجلسات بهالأيام طبيعي مش فجوة
US_HOLIDAYS = {"2019-11-28", "2019-12-25", "2020-01-01", "2020-01-20", "2020-02-17"}
_cache = {}


def _load():
    if "df" in _cache:
        return _cache["df"], _cache["chargers"]
    conn = get_conn()
    chargers = pd.read_sql_query("SELECT * FROM chargers", conn)
    df = pd.read_sql_query(
        "SELECT s.*, c.region FROM charging_sessions s JOIN chargers c ON s.charger_id = c.charger_id", conn)
    conn.close()
    df["day"] = pd.to_datetime(df["day"])
    _cache["df"], _cache["chargers"] = df, chargers
    return df, chargers


def _err(tool, msg):
    return {"tool": tool, "error": msg}


def _prep(tool, region, as_of_day):
    """بيرجع (df للمنطقة لحد as_of, as_of, all_df لحد as_of) أو (None, رسالة خطأ, None)"""
    df, chargers = _load()
    if region not in set(chargers["region"]):
        return None, f"unknown region '{region}'. Known: {sorted(set(chargers['region']))}", None
    last = df["day"].max()
    if as_of_day:
        try:
            as_of = pd.to_datetime(as_of_day)
        except (ValueError, TypeError):
            return None, f"invalid as_of_day '{as_of_day}'. Expected format YYYY-MM-DD", None
    else:
        as_of = last
    all_df = df[df["day"] <= as_of]
    return all_df[all_df["region"] == region], as_of, all_df


def _daily(reg_df, as_of, days_back_start, days_back_end):
    """مجموع الطاقة اليومي (kWh) لنافذة أيام، الأيام الفاضية = 0"""
    start = as_of - pd.Timedelta(days=days_back_start - 1)
    end = as_of - pd.Timedelta(days=days_back_end)
    idx = pd.date_range(start, end, freq="D")
    s = reg_df[(reg_df["day"] >= start) & (reg_df["day"] <= end)].groupby("day")["kwh_delivered"].sum()
    return s.reindex(idx, fill_value=0.0)


def _enough_history(all_df, as_of):
    first = _load()[0]["day"].min()
    return (as_of - first).days + 1 >= RECENT + BASE


def _short(tool):
    return {"tool": tool, "strength": 0.0, "flagged": False,
            "summary": f"Not enough history: need {RECENT + BASE} days before the date to compare."}


# ---------------------------- الأدوات ----------------------------

def get_region_charging_summary(region, as_of_day=None):
    tool = "get_region_charging_summary"
    reg, as_of, _ = _prep(tool, region, as_of_day)
    if reg is None:
        return _err(tool, as_of)
    start = as_of - pd.Timedelta(days=RECENT - 1)
    rec = reg[reg["day"] >= start]
    n_ch = int(reg["charger_id"].nunique())
    kwh = float(rec["kwh_delivered"].sum())
    return {
        "tool": tool, "region": region, "as_of_day": str(as_of.date()), "chargers": n_ch,
        "sessions_last_14d": int(len(rec)), "kwh_per_day_last_14d": round(kwh / RECENT, 1),
        "strength": 0.0, "flagged": False,
        "summary": f"{region}: {n_ch} chargers, {len(rec)} sessions in the last {RECENT} days, "
                   f"{kwh / RECENT:.1f} kWh/day delivered.",
    }


def detect_charging_load_shift(region, as_of_day=None):
    """هل حمل الشحن بالمنطقة تغيّر (زاد أو نقص) مقارنة بالأسابيع اللي قبلها؟"""
    tool = "detect_charging_load_shift"
    reg, as_of, all_df = _prep(tool, region, as_of_day)
    if reg is None:
        return _err(tool, as_of)
    if not _enough_history(all_df, as_of):
        return _short(tool)
    rec = _daily(reg, as_of, RECENT, 0)
    base = _daily(reg, as_of, RECENT + BASE, RECENT)
    b_mean, r_mean = float(base.mean()), float(rec.mean())
    if b_mean <= 0:
        return _err(tool, "no baseline charging in this region")
    ratio = r_mean / b_mean
    se = float(base.std()) / np.sqrt(RECENT)
    z = (r_mean - b_mean) / se if se > 0 else 0.0
    flagged = bool(abs(ratio - 1) >= LOAD_RATIO and abs(z) >= LOAD_Z)
    strength = float(np.clip((abs(ratio - 1) - 0.15) / 0.6, 0, 1)) if flagged else 0.0
    direction = "up" if ratio > 1 else "down"
    return {
        "tool": tool, "region": region, "baseline_kwh_per_day": round(b_mean, 1),
        "recent_kwh_per_day": round(r_mean, 1), "ratio": round(ratio, 2), "z": round(float(z), 1),
        "direction": direction, "extra_kwh_per_day": round(r_mean - b_mean, 1),
        "strength": round(strength, 2), "flagged": flagged,
        "summary": (f"{region} charging {b_mean:.1f} -> {r_mean:.1f} kWh/day (x{ratio:.2f}, z={z:.1f}). "
                    + (f"Charging load went {direction} significantly." if flagged else "No significant change.")),
    }


def detect_new_charger_activity(region, as_of_day=None):
    """هل في شاحن كان شبه ساكت وبلّش يشتغل مؤخراً؟ (ممكن يفسر ارتفاع بالاستهلاك)"""
    tool = "detect_new_charger_activity"
    reg, as_of, all_df = _prep(tool, region, as_of_day)
    if reg is None:
        return _err(tool, as_of)
    if not _enough_history(all_df, as_of):
        return _short(tool)
    rstart = as_of - pd.Timedelta(days=RECENT - 1)
    bstart = as_of - pd.Timedelta(days=RECENT + BASE - 1)
    rec = reg[reg["day"] >= rstart]
    base = reg[(reg["day"] >= bstart) & (reg["day"] < rstart)]
    new = []
    for cid in reg["charger_id"].unique():
        nb = int((base["charger_id"] == cid).sum())
        nr = int((rec["charger_id"] == cid).sum())
        kr = float(rec.loc[rec["charger_id"] == cid, "kwh_delivered"].sum())
        if nb <= 2 and nr >= 6:
            new.append({"charger_id": cid, "sessions_before": nb, "sessions_now": nr,
                        "kwh_per_day_now": round(kr / RECENT, 1)})
    total_now = float(rec["kwh_delivered"].sum()) or 1.0
    new_kwh = sum(n["kwh_per_day_now"] for n in new) * RECENT
    share = new_kwh / total_now
    flagged = bool(new)
    strength = float(np.clip(share / 0.4, 0, 1)) if flagged else 0.0
    return {
        "tool": tool, "region": region, "newly_active_chargers": new,
        "share_of_recent_kwh": round(share, 2),
        "strength": round(strength, 2), "flagged": flagged,
        "summary": (f"{len(new)} charger(s) in {region} were almost idle for {BASE} days and became active "
                    f"({share:.0%} of recent energy): " + ", ".join(n['charger_id'] for n in new)
                    if flagged else f"No charger in {region} newly became active."),
    }
    ct.find_top_charging_contributors(region, day),

def compare_charger_failure_rate(region, as_of_day=None):
    """هل في شاحن نسبة جلساته الفاشلة (اتصال طويل بدون طاقة) أعلى بكثير من باقي الشواحن؟"""
    tool = "compare_charger_failure_rate"
    reg, as_of, all_df = _prep(tool, region, as_of_day)
    if reg is None:
        return _err(tool, as_of)
    start = as_of - pd.Timedelta(days=FAIL_WINDOW - 1)
    win_all = all_df[all_df["day"] >= start].copy()
    if len(win_all) < 50:
        return _short(tool)
    fail = (win_all["kwh_delivered"] < FAIL_KWH) & (win_all["duration_min"].fillna(0) >= FAIL_MIN)
    win_all["fail"] = fail
    fleet_rate = float(win_all["fail"].mean())
    win_reg = win_all[win_all["region"] == region]
    rows = []
    for cid, g in win_reg.groupby("charger_id"):
        n = int(len(g))
        if n >= 8:
            rows.append({"charger_id": cid, "sessions": n, "failed": int(g["fail"].sum()),
                         "failure_rate": round(float(g["fail"].mean()), 2)})
    rows.sort(key=lambda r: -r["failure_rate"])
    worst = rows[0] if rows else None
    gap = (worst["failure_rate"] - fleet_rate) if worst else 0.0
    flagged = bool(worst and gap >= 0.2)
    strength = float(np.clip(gap / 0.5, 0, 1)) if flagged else 0.0
    return {
        "tool": tool, "region": region, "fleet_failure_rate": round(fleet_rate, 2),
        "worst_chargers": rows[:3],
        "strength": round(strength, 2), "flagged": flagged,
        "summary": (f"Fleet failure rate {fleet_rate:.0%}. "
                    + (f"Charger {worst['charger_id']} fails {worst['failure_rate']:.0%} of sessions "
                       f"({worst['failed']}/{worst['sessions']}): likely faulty."
                       if flagged else "No charger in the region fails clearly more than the fleet.")),
    }


def _charger_extras(reg, as_of):
    """لكل شاحن: الزيادة اليومية (kWh/day) و z (هل الزيادة أكبر من تذبذبه العادي؟)"""
    rstart = as_of - pd.Timedelta(days=RECENT - 1)
    bstart = as_of - pd.Timedelta(days=RECENT + BASE - 1)
    days = pd.date_range(bstart, as_of, freq="D")
    daily = (reg[reg["day"] >= bstart].groupby(["charger_id", "day"])["kwh_delivered"].sum()
             .unstack(fill_value=0.0).reindex(columns=days, fill_value=0.0))
    rec = daily.loc[:, daily.columns >= rstart]
    base = daily.loc[:, daily.columns < rstart]
    extra = rec.mean(axis=1) - base.mean(axis=1)
    se = base.std(axis=1) / np.sqrt(RECENT)
    z = (extra / se.replace(0, np.nan)).fillna(0.0)
    return pd.DataFrame({"extra": extra, "z": z}).sort_values("extra", ascending=False)


def find_top_charging_contributors(region, as_of_day=None):
    """أي شواحن بالمنطقة زادت أكثر شي، وهل زيادتها ملحوظة؟ (سياق: ما بيرفع flagged لحاله)"""
    tool = "find_top_charging_contributors"
    reg, as_of, all_df = _prep(tool, region, as_of_day)
    if reg is None:
        return _err(tool, as_of)
    if not _enough_history(all_df, as_of):
        return _short(tool)
    ex = _charger_extras(reg, as_of)
    total = float(ex["extra"].sum())
    top = [{"charger_id": c, "extra_kwh_per_day": round(float(r["extra"]), 1), "z": round(float(r["z"]), 1),
            "significant": bool(r["z"] >= CHARGER_Z)} for c, r in ex.head(3).iterrows()]
    return {
        "tool": tool, "region": region, "region_extra_kwh_per_day": round(total, 1),
        "top_chargers": top, "strength": 0.0, "flagged": False,
        "summary": (f"{region} charging changed by {total:+.1f} kWh/day in total. Top chargers: "
                    + ", ".join(f"{t['charger_id']} ({t['extra_kwh_per_day']:+.1f}, z={t['z']})" for t in top) + "."),
    }


def explain_meter_change(region, meter_delta_kwh_per_day, as_of_day=None):
    """هل زيادة ملحوظة بأحد شواحن المنطقة بتكافئ تغيّر العداد بالحجم؟ (رقم العداد من نص الحادثة)"""
    tool = "explain_meter_change"
    reg, as_of, all_df = _prep(tool, region, as_of_day)
    if reg is None:
        return _err(tool, as_of)
    if not _enough_history(all_df, as_of):
        return _short(tool)
    delta = float(meter_delta_kwh_per_day)
    ex = _charger_extras(reg, as_of)
    cand = ex[ex["z"] >= CHARGER_Z].head(3)          # بس الشواحن اللي زيادتها ملحوظة فعلاً
    best = None
    for cid, r in cand.iterrows():
        frac = float(r["extra"]) / delta if delta > 0 else 0.0
        if best is None or abs(frac - 1) < abs(best[1] - 1):
            best = (cid, frac, float(r["extra"]), float(r["z"]))
    flagged = bool(delta > 0 and best and 0.5 <= best[1] <= 2.0)
    strength = 0.9 * (1 - min(abs(best[1] - 1), 1.0)) if flagged else 0.0
    if flagged:
        summary = (f"Charger {best[0]} added {best[2]:.1f} kWh/day (z={best[3]:.1f}, a significant rise) vs the meter "
                   f"change of {delta:.1f} kWh/day ({best[1]:.0%}): the size matches, so charging can explain it "
                   f"(location match not verified).")
    elif len(cand) == 0:
        summary = (f"No charger in {region} increased significantly (z >= {CHARGER_Z}), so a size match with the "
                   f"meter change of {delta:.1f} kWh/day would be a coincidence.")
    else:
        summary = f"No significantly increased charger in {region} matches the meter change of {delta:.1f} kWh/day in size."
    return {
        "tool": tool, "region": region, "meter_change_kwh_per_day": round(delta, 1),
        "significant_chargers": int(len(cand)),
        "best_matching_charger": best[0] if best else None,
        "charger_extra_kwh_per_day": round(best[2], 1) if best else 0.0,
        "fraction_explained": round(best[1], 2) if best else 0.0,
        "region_extra_kwh_per_day": round(float(ex["extra"].sum()), 1),
        "strength": round(strength, 2), "flagged": flagged, "summary": summary,
    }

def check_charging_data_quality(region, as_of_day=None):
    """هل داتا الشحن ناقصة أو فيها فجوات؟ إذا إي: refutes (الفرضية بتضعف)"""
    tool = "check_charging_data_quality"
    reg, as_of, all_df = _prep(tool, region, as_of_day)
    if reg is None:
        return _err(tool, as_of)
    start = as_of - pd.Timedelta(days=FAIL_WINDOW - 1)
    win = reg[reg["day"] >= start]
    if len(win) == 0:
        return {"tool": tool, "strength": 0.7, "flagged": True, "refutes": True,
                "summary": f"No sessions at all in {region} in the last {FAIL_WINDOW} days: data gap or outage."}
    miss_disc = float(win["disconnect_time"].isna().mean())
    neg = int((win["duration_min"].fillna(0) < 0).sum())
    days = pd.date_range(start, as_of, freq="D")
    active = set(all_df[all_df["day"] >= start]["day"])
    gap_weekdays = [d for d in days if d.weekday() < 5 and d not in active
                    and str(d.date()) not in US_HOLIDAYS]
    flagged = bool(gap_weekdays or miss_disc > 0.3 or neg > 0)
    return {
        "tool": tool, "missing_disconnect_fraction": round(miss_disc, 2), "negative_durations": neg,
        "network_gap_weekdays": [str(d.date()) for d in gap_weekdays],
        "strength": 0.7 if flagged else 0.0, "flagged": flagged, "refutes": flagged,
        "summary": ("Charging data has problems (gap days / missing or invalid times); conclusions are weak."
                    if flagged else "Charging data looks complete (no gap weekdays, no invalid times)."),
    }



def find_real_chargers_near_region(region):
    """محطات شحن حقيقية بالأردن (Open Charge Map) قرب المنطقة: مصدر خارجي، سياق فقط"""
    tool = "find_real_chargers_near_region"
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT title, town, num_points, max_power_kw FROM jordan_chargers WHERE region = ? "
            "ORDER BY COALESCE(max_power_kw, 0) DESC, title", (region,)).fetchall()
    except Exception:  # noqa: BLE001
        conn.close()
        return _err(tool, "jordan_chargers table missing; run: python db/seed_ocm.py")
    conn.close()
    if not rows:
        return {"tool": tool, "region": region, "stations": 0, "strength": 0.0, "flagged": False,
                "summary": f"No Open Charge Map station found within 30 km of {region}."}
    examples = [{"title": r["title"], "town": r["town"], "max_power_kw": r["max_power_kw"]} for r in rows[:3]]
    powers = [r["max_power_kw"] for r in rows if r["max_power_kw"]]
    max_kw = float(max(powers)) if powers else 0.0
    points = int(sum((r["num_points"] or 0) for r in rows))
    names = ", ".join(e["title"] for e in examples if e["title"])
    return {
        "tool": tool, "region": region, "stations": int(len(rows)), "charging_points": points,
        "max_power_kw": max_kw, "examples": examples, "source": "Open Charge Map",
        "strength": 0.0, "flagged": False,
        "summary": (f"{region}: {len(rows)} real public charging stations in Open Charge Map within 30 km "
                    f"(up to {max_kw:.0f} kW), e.g. {names}."),
    }

# get_region_charging_summary	كم شاحن وجلسة وكيلوواط ساعة باليوم؟
# detect_charging_load_shift	هل حمل الشحن زاد أو نقص بشكل ملحوظ؟
# detect_new_charger_activity	هل في شاحن كان ساكت وبلّش يشتغل؟
# compare_charger_failure_rate	هل في شاحن جلساته الفاشلة أعلى بكثير من غيره؟
# explain_meter_change	هل زيادة الشحن بتكفي لتفسير زيادة العداد؟ (الرقم بيجي من نص الحادثة)
# check_charging_data_quality	هل الداتا فيها فجوات؟ إذا إي بترجع refutes