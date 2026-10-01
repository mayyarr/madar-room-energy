import pandas as pd
from db.database import get_conn


def _load_meter(meter_id):
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT day, kwh_total, readings_count FROM meter_readings "
        "WHERE meter_id = ? ORDER BY day",
        conn, params=(meter_id,),
    )
    conn.close()
    df["day"] = pd.to_datetime(df["day"])
    return df


def get_meter_summary(meter_id):
    """ملخص عام عن العداد: منطقته، فترة قراءاته، ومتوسط استهلاكه اليومي."""
    conn = get_conn()
    row = conn.execute(
        "SELECT region FROM meters WHERE meter_id = ?", (meter_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"tool": "get_meter_summary", "error": f"Unknown meter {meter_id}"}

    df = _load_meter(meter_id)
    if df.empty:
        return {"tool": "get_meter_summary", "meter_id": meter_id, "error": "No data"}
    return {
        "tool": "get_meter_summary",
        "meter_id": meter_id,
        "region": row["region"],
        "first_day": str(df["day"].min().date()),
        "last_day": str(df["day"].max().date()),
        "days_of_data": int(len(df)),
        "avg_daily_kwh": round(float(df["kwh_total"].mean()), 2),
    }


def detect_consumption_drop(meter_id, window=14, baseline=60, threshold_pct=40):
    """
    يقارن متوسط آخر `window` يوماً بمتوسط `baseline` يوماً قبلها.
    هبوط حاد قد يعني عطل عداد أو تلاعباً أو انقطاع تغذية.
    strength: قوة الدليل من 0 إلى 1.
    """
    df = _load_meter(meter_id)
    if len(df) < window + 14:
        return {"tool": "detect_consumption_drop", "meter_id": meter_id,
                "error": "Not enough data"}

    recent = df.tail(window)["kwh_total"]
    earlier = df.iloc[:-window].tail(baseline)["kwh_total"]
    recent_avg, baseline_avg = float(recent.mean()), float(earlier.mean())
    drop_pct = (baseline_avg - recent_avg) / baseline_avg * 100 if baseline_avg else 0.0
    flagged = drop_pct >= threshold_pct

    return {
        "tool": "detect_consumption_drop",
        "meter_id": meter_id,
        "recent_avg_kwh": round(recent_avg, 2),
        "baseline_avg_kwh": round(baseline_avg, 2),
        "drop_pct": round(float(drop_pct), 1),
        "flagged": bool(flagged),
        "strength": round(float(max(0.0, min(1.0, drop_pct / 80))), 2),
        "summary": (
            f"Consumption dropped {drop_pct:.0f}% vs baseline"
            if flagged else "No significant drop"
        ),
    }


def compare_neighbor_meters(meter_id, window=14):
    """
    يقارن استهلاك العداد الأخير بعدادات نفس المنطقة في نفس الفترة.
    z_score بعيد عن الصفر يعني أن العداد يشذ عن جيرانه.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT region FROM meters WHERE meter_id = ?", (meter_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"tool": "compare_neighbor_meters", "error": f"Unknown meter {meter_id}"}

    df = _load_meter(meter_id)
    end = df["day"].max()
    start = end - pd.Timedelta(days=window)

    peers = pd.read_sql_query(
        "SELECT r.meter_id, AVG(r.kwh_total) AS avg_kwh "
        "FROM meter_readings r JOIN meters m ON m.meter_id = r.meter_id "
        "WHERE m.region = ? AND r.meter_id != ? AND r.day BETWEEN ? AND ? "
        "GROUP BY r.meter_id",
        conn, params=(row["region"], meter_id, str(start.date()), str(end.date())),
    )
    conn.close()

    own = float(df[df["day"] > start]["kwh_total"].mean())
    if len(peers) < 3:
        return {"tool": "compare_neighbor_meters", "meter_id": meter_id,
                "error": "Not enough neighbors in this period"}

    peer_mean, peer_std = float(peers["avg_kwh"].mean()), float(peers["avg_kwh"].std())
    z = float((own - peer_mean) / peer_std) if peer_std else 0.0

    return {
        "tool": "compare_neighbor_meters",
        "meter_id": meter_id,
        "region": row["region"],
        "own_avg_kwh": round(own, 2),
        "neighbors_avg_kwh": round(peer_mean, 2),
        "neighbors_count": int(len(peers)),
        "z_score": round(z, 2),
        "flagged": bool(abs(z) >= 2),
        "strength": round(float(min(1.0, abs(z) / 4)), 2),
    }


def _peer_avg(conn, region, meter_id, start, end):
    """متوسط استهلاك عدادات المنطقة (بدون هذا العداد) بين تاريخين."""
    peers = pd.read_sql_query(
        "SELECT r.meter_id, AVG(r.kwh_total) AS avg_kwh "
        "FROM meter_readings r JOIN meters m ON m.meter_id = r.meter_id "
        "WHERE m.region = ? AND r.meter_id != ? AND r.day BETWEEN ? AND ? "
        "GROUP BY r.meter_id",
        conn, params=(region, meter_id, str(start.date()), str(end.date())),
    )
    return (float(peers["avg_kwh"].mean()), int(len(peers))) if len(peers) else (None, 0)


def compare_history_vs_neighbors(meter_id, window=14, baseline=90):
    """
    يفحص هل العداد كان دائماً بهالمستوى مقارنة بجيرانه، أم أن التغيّر جديد.
    - new_high: ارتفع فجأة فوق جيرانه (استهلاك غير طبيعي/تلاعب).
    - new_drop: هبط فجأة تحت جيرانه (عطل/تلاعب/انقطاع).
    - chronic_*: كان هيك دائماً، فهو مستهلك كبير/صغير بطبيعته، وليس عطلاً جديداً.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT region FROM meters WHERE meter_id = ?", (meter_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"tool": "compare_history_vs_neighbors", "error": f"Unknown meter {meter_id}"}

    df = _load_meter(meter_id)
    end = df["day"].max()
    recent_start = end - pd.Timedelta(days=window)
    base_start = recent_start - pd.Timedelta(days=baseline)

    own_recent = float(df[df["day"] > recent_start]["kwh_total"].mean())
    own_base = float(
        df[(df["day"] > base_start) & (df["day"] <= recent_start)]["kwh_total"].mean()
    )
    peer_recent, n1 = _peer_avg(conn, row["region"], meter_id, recent_start, end)
    peer_base, n2 = _peer_avg(conn, row["region"], meter_id, base_start, recent_start)
    conn.close()

    if not peer_recent or not peer_base or n1 < 3 or n2 < 3 or pd.isna(own_recent) or pd.isna(own_base):
        return {"tool": "compare_history_vs_neighbors", "meter_id": meter_id,
                "error": "Not enough neighbor data"}

    recent_ratio = float(own_recent / peer_recent)
    hist_ratio = float(own_base / peer_base)

    new_high = bool(recent_ratio >= 1.8 and hist_ratio < 1.3)
    new_drop = bool(recent_ratio <= 0.55 and hist_ratio >= 0.8)
    chronic_high = bool(recent_ratio >= 1.8 and hist_ratio >= 1.8)
    chronic_low = bool(recent_ratio <= 0.55 and hist_ratio <= 0.55)

    if new_high:
        direction, strength = "up", (recent_ratio - hist_ratio) / 2
        summary = (f"Consumption is newly abnormal: now {recent_ratio:.2f}x the neighbors' "
                   f"average (was {hist_ratio:.2f}x).")
    elif new_drop:
        direction, strength = "down", (hist_ratio - recent_ratio) / max(hist_ratio, 0.01) / 0.8
        summary = (f"Consumption newly dropped below neighbors: now {recent_ratio:.2f}x their "
                   f"average (was {hist_ratio:.2f}x).")
    elif chronic_high:
        direction, strength = "none", 0.0
        summary = "Meter has ALWAYS been much higher than neighbors (likely a heavy consumer, not a new fault)."
    elif chronic_low:
        direction, strength = "none", 0.0
        summary = "Meter has ALWAYS been much lower than neighbors (likely a small or vacant site, not a new fault)."
    else:
        direction, strength = "none", 0.0
        summary = "No clear change relative to neighbors."

    new_anomaly = bool(new_high or new_drop)
    return {
        "tool": "compare_history_vs_neighbors",
        "meter_id": meter_id,
        "recent_ratio_vs_neighbors": round(recent_ratio, 2),
        "historical_ratio_vs_neighbors": round(hist_ratio, 2),
        "new_anomaly": new_anomaly,
        "direction": direction,
        "chronic_high_consumer": chronic_high,
        "chronic_low_consumer": chronic_low,
        "flagged": new_anomaly,
        "refutes": bool(chronic_high or chronic_low),
        "strength": round(float(min(1.0, max(0.0, strength))), 2),
        "summary": summary,
    }


def check_data_quality(meter_id, window=30):
    """
    يفحص جودة القراءات في آخر `window` يوماً: أيام ناقصة أو قراءات غير مكتملة.
    اليوم الكامل فيه 48 قراءة (كل نصف ساعة).
    مشاكل الجودة تدعم فرضية عطل العداد أو الاتصال.
    """
    df = _load_meter(meter_id)
    if df.empty:
        return {"tool": "check_data_quality", "meter_id": meter_id, "error": "No data"}
    if window <= 0:
        return {"tool": "check_data_quality", "meter_id": meter_id, "error": "window must be a positive number of days"}

    end = df["day"].max()
    recent = df[df["day"] > end - pd.Timedelta(days=window)]
    missing_days = window - len(recent)
    incomplete_days = int((recent["readings_count"] < 44).sum())
    problem_share = (max(missing_days, 0) + incomplete_days) / window

    return {
        "tool": "check_data_quality",
        "meter_id": meter_id,
        "window_days": window,
        "missing_days": int(max(missing_days, 0)),
        "incomplete_days": incomplete_days,
        "flagged": bool(problem_share >= 0.1),
        "strength": round(float(min(1.0, problem_share / 0.3)), 2),
        "summary": (
            "Data gaps detected" if problem_share >= 0.1 else "Readings are complete"
        ),
    }


# شو تعمل الأدوات؟
# get_meter_summary: صورة أولية عن العداد.
# detect_consumption_drop: آخر 14 يوماً مقابل الـ 60 قبلها (المرجع آخر يوم عند العداد نفسه لأن الداتا قديمة 2011-2014). هبوط 40% فأكثر = مؤشر.
# compare_neighbor_meters: مقارنة مع عدادات المنطقة بنفس الفترة (z_score).
# compare_history_vs_neighbors: هل التغيّر جديد (ارتفاع أو هبوط) أم العداد هيك دائماً؟
# check_data_quality: أيام ناقصة أو قراءات غير مكتملة.
# كل أداة ترجع strength (0-1) وهو اللي بيتغذى منه confidence.py