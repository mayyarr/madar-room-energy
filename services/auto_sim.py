"""
services/auto_sim.py - محاكاة الكشف التلقائي (زر "تشغيل تلقائي" بالواجهة)

الفكرة: إثبات إنه النظام بيقدر يكتشف شذوذ ويحقق فيه لحاله بدون أي تدخل بشري.
ما بيشتغل أبداً لحاله — بس لما ينضغط الزر. وبكل ضغطة: جولة وحدة بس (حادثة وحدة)،
عشان نوفر توكنز Groq وما تتداخل الحالات مع بعض.

كيف بيشتغل:
1. "ساعة محاكاة" داخلية (مش وقت حقيقي، لأنه الداتا تاريخية ثابتة) بتبلش من
   SIM_START_DAY وبتتقدم يوم محاكاة واحد كل TICK_SECONDS.
2. بكل تيكة بيفحص مصدرين إلهم زمن فعلي بالداتا، بنفس الأدوات الحقيقية:
   - الشحن (ACN-Data): الـ 8 مناطق — detect_charging_load_shift,
     detect_new_charger_activity, compare_charger_failure_rate (منطق scan_charging.py)
   - المحطات (C-MAPSS): الـ 100 وحدة عند "دورة الأسطول" الحالية —
     detect_thermal_anomaly ثم get_degradation_trend وcheck_sensor_quality
3. أول ما ينكشف شذوذ، بيعمل فحص تقاطعي لعدادات نفس المنطقة
   (compare_history_vs_neighbors، منطق scan_meters.py). لو في عداد شاذ كمان
   -> حادثة "عبر المجالات" (شحن + عداد).
4. بيبني نص الحادثة من أرقام الأدوات الحقيقية، وبيسلّمه لنفس مسار التحقيق
   العادي (run_and_save عبر run_fn) — الوكلاء والتحدي والتحكيم ما تغيّروا إطلاقاً.

التوقيت محسوب مسبقاً: أول إشارة حقيقية بالداتا هي شحن مأدبا يوم 2020-02-14
(تم التحقق بالفحص). البداية 2020-02-10، فالكشف بيصير عند التيكة الرابعة
= 4 × 1.5 ثانية = 6 ثواني من الضغط (+ أقل من ثانية للفحص التقاطعي للعدادات).

العدادات ما بتنفحص بكل تيكة لحالها لأنه داتا لندن (2011-2014) ما إلها تاريخ مقابل
بفترة المحاكاة، وأدوات العداد بتقارن دايماً آخر أيام العداد نفسه — فبتُستخدم
كتأكيد تقاطعي للمنطقة يلي انكشف فيها شذوذ، مش كمصدر توقيت.
"""
import threading
import time

import pandas as pd

from tools import charging_tools as ct
from tools import plant_tools as pt
from tools import meter_tools as mt
from db.database import get_conn

SIM_START_DAY = "2020-02-10"   # يوم قبل الحادثة المعروفة بـ 4 أيام محاكاة
# نقاط بداية مختلفة (كل وحدة قبل إشارة حقيقية بالداتا بـ 4 أيام، متحقق منها بالفحص):
# الطفيلة 01-17، معان 02-04، جرش 02-10، مادبا 02-14. كل ضغطة بتاخد اللي بعدها بالدور.
SIM_START_DAYS = ["2020-01-31"]   # معان فقط (إشارة شحن حقيقية 2020-02-04)
_rot = {"i": 0}
SIM_START_CYCLE = 60           # دورة أسطول المحطات بأول يوم محاكاة (+1 دورة لكل يوم)
TICK_SECONDS = 1.5             # مدة يوم المحاكاة الواحد (بالثواني الحقيقية)
MAX_TICKS = 20                 # حد أمان: لو ما انكشف شي خلال 20 يوم محاكاة نوقف

from db.regions import REGIONS, REGION_AR  # noqa: E402

_lock = threading.Lock()
_state = {"running": False, "stop": False, "phase": "idle"}


def status():
    with _lock:
        return dict(_state)


def _set(**kw):
    with _lock:
        _state.update(kw)


# ---------------- الفحص لكل مصدر ----------------

def _scan_charging(day):
    hits = []
    for reg in REGIONS:
        for fn in (ct.detect_charging_load_shift, ct.detect_new_charger_activity,
                   ct.compare_charger_failure_rate):
            r = fn(reg, day)
            if r.get("flagged") and not r.get("error"):
                hits.append({"source": "charging", "region": reg, "tool": r["tool"],
                             "strength": r.get("strength", 0), "result": r})
    return hits


def _scan_plant(cycle):
    hits = []
    units = [r["unit_id"] for r in _plant_units()]
    for u in units:
        th = pt.detect_thermal_anomaly(u, cycle)
        if not th.get("flagged"):
            continue
        q = pt.check_sensor_quality(u, cycle)
        dg = pt.get_degradation_trend(u, cycle)
        if dg.get("flagged") and not q.get("flagged"):
            hits.append({"source": "plant", "unit_id": int(u), "tool": th["tool"],
                         "strength": th.get("strength", 0), "result": th, "trend": dg})
    return hits


_units_cache = []


def _plant_units():
    if not _units_cache:
        conn = get_conn()
        _units_cache.extend(dict(r) for r in conn.execute("SELECT unit_id, site FROM plant_units"))
        conn.close()
    return _units_cache


def _cross_check_meters(region):
    """عدادات نفس المنطقة: أقوى عداد صار شاذ حديثاً (ارتفاع أو هبوط) مقارنة بجيرانه."""
    conn = get_conn()
    ids = [r["meter_id"] for r in conn.execute("SELECT meter_id FROM meters WHERE region = ?", (region,))]
    conn.close()
    best = None
    for mid in ids:
        h = mt.compare_history_vs_neighbors(mid)
        if h.get("new_anomaly") and (best is None or h["strength"] > best["strength"]):
            best = h
    if not best:
        return None
    d = mt.detect_consumption_drop(best["meter_id"])
    best["recent_avg_kwh"] = d.get("recent_avg_kwh")
    best["baseline_avg_kwh"] = d.get("baseline_avg_kwh")
    return best


# ---------------- بناء نص الحادثة ----------------

def _build_incident(day, cycle, charging_hits, plant_hits):
    if charging_hits:
        top = max(charging_hits, key=lambda h: h["strength"])
        reg = top["region"]
        meter = _cross_check_meters(reg)
        r = top["result"]
        parts = [f"Automated monitoring alert on {day}: {r['summary']}"]
        sources = ["charging"]
        if meter and meter.get("recent_avg_kwh") is not None:
            verb = "rose" if meter["direction"] == "up" else "dropped"
            parts.append(
                f"Cross-check: Meter {meter['meter_id']} in {reg} region {verb} from "
                f"{meter['baseline_avg_kwh']} to {meter['recent_avg_kwh']} kWh/day as of {day}."
            )
            sources.append("meter")
        parts.append("Investigate the cause.")
        kinds = " + ".join({"charging": "شحن", "meter": "عداد"}[s] for s in sources)
        title = f"🤖 كشف تلقائي — {kinds} · {REGION_AR.get(reg, reg)}"
        return {"title": title, "event_text": " ".join(parts), "region": reg,
                "sources": sources, "meter_id": meter["meter_id"] if meter else None}

    top = max(plant_hits, key=lambda h: h["strength"])
    u = top["unit_id"]
    site = next((x["site"] for x in _plant_units() if x["unit_id"] == u), "")
    region = "Aqaba" if "العقبة" in site else ("Zarqa" if "الزرقاء" in site else None)
    text = (f"Automated monitoring alert: unit {u} shows abnormal temperatures "
            f"({top['result']['summary']}). Investigate the unit as of cycle {cycle}.")
    return {"title": f"🤖 كشف تلقائي — معدّات · Unit {u}", "event_text": text,
            "region": region, "sources": ["plant"], "meter_id": None}


# ---------------- الحلقة ----------------

def _loop(run_fn, publish):
    with _lock:
        start_day = SIM_START_DAYS[_rot["i"] % len(SIM_START_DAYS)]
        _rot["i"] += 1
    day0 = pd.Timestamp(start_day)
    try:
        publish({"type": "auto_started", "start_day": start_day, "tick_seconds": TICK_SECONDS})
        t0 = time.monotonic()
        for i in range(1, MAX_TICKS + 1):
            # إيقاع ثابت: التيكة رقم i بتصير عند i × TICK_SECONDS بالضبط من الضغط،
            # بغض النظر عن وقت الفحص نفسه (هيك التوقيت محسوب ومضمون)
            while time.monotonic() - t0 < i * TICK_SECONDS:
                if status()["stop"]:
                    publish({"type": "auto_stopped", "phase": "scanning"})
                    return
                time.sleep(0.05)

            day = str((day0 + pd.Timedelta(days=i)).date())
            cycle = SIM_START_CYCLE + i
            ch = _scan_charging(day)
            pl = _scan_plant(cycle)
            publish({"type": "auto_tick", "day": day, "cycle": cycle, "tick": i,
                     "scanned": {"regions": len(REGIONS), "units": len(_plant_units())},
                     "signals": len(ch) + len(pl)})

            if ch or pl:
                if status()["stop"]:
                    publish({"type": "auto_stopped", "phase": "scanning"})
                    return
                inc = _build_incident(day, cycle, ch, pl)
                _set(phase="investigating")
                publish({"type": "auto_detected", "day": day, **inc})
                run_fn(inc["event_text"], inc["title"], inc["region"])
                publish({"type": "auto_finished"})
                return

        publish({"type": "auto_finished", "note": "no anomaly found in the simulated window"})
    except Exception as e:  # noqa: BLE001
        publish({"type": "auto_error", "error": str(e)})
    finally:
        _set(running=False, stop=False, phase="idle")


def start(run_fn, publish):
    """يبلش جولة وحدة. يرجع False لو في جولة شغالة أصلاً (منع التداخل)."""
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, stop=False, phase="scanning")
    threading.Thread(target=_loop, args=(run_fn, publish), daemon=True).start()
    return True


def stop():
    """
    يوقف الجولة. لو لسا بمرحلة الفحص -> بتوقف فوراً وما بيتفتح أي تحقيق.
    لو التحقيق بلش فعلاً -> نداءات Groq الجارية ما بتنقطع بالنص، فالتحقيق بيكمل
    ويتسجّل عادي، والواجهة بتعرف هاد من phase="investigating".
    """
    with _lock:
        if not _state["running"]:
            return {"status": "idle"}
        _state["stop"] = True
        return {"status": "stopping", "phase": _state["phase"]}
