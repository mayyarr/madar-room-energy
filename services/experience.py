"""
services/experience.py - ذاكرة الخبرة: النظام بيتعلم من كل حادثة بيحقق فيها

الفكرة: كل تحقيق بيخلص بيتسجّل "درس" (أي أصل، شو الأدلة وأرقامها، شو قال كل وكيل،
كيف انحسمت المواجهة). لما تيجي حادثة جديدة على نفس الأصل وبنفس ظروف البيانات:
  1. النظام بيعيد تشغيل الأدوات الحسابية نفسها بس (بدون أي نداء Groq) — ثواني.
  2. لو الأدلة لسا مطابقة للدرس (نفس الإشارات وقوة قريبة) → بيستعمل خبرته:
     فرضيات الوكلاء وتحدياتهم من الدرس، والثقة بتنحسب من جديد بالكود على الأدلة
     الطازجة. فالنتيجة بتطلع أسرع بكثير وبدون صرف توكنز.
  3. لو الأدلة تغيّرت، أو القرار السابق انرفض من إنسان، أو وكيل كان فاشل →
     ما في اختصار: تحقيق كامل من أول وجديد (والدرس الجديد بيحل محل القديم).

كل شي هون داخلي — ما في أي حدث أو عنصر بالواجهة بيحكي إنه النتيجة "من الخبرة".
الجدول experience مش ضمن الجداول المسموحة لـ VOLT-AI.
"""
import hashlib
import json
import re
import threading
import time

from db.database import get_conn

STRENGTH_TOL = 0.05
_lock = threading.Lock()


def _json_default(o):
    try:
        return o.item()          # numpy scalars
    except Exception:  # noqa: BLE001
        return str(o)


def _table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS experience (
            key TEXT PRIMARY KEY,
            incident_id INTEGER,
            results_json TEXT,
            challenges_json TEXT,
            duration_s REAL,
            uses INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)


def case_key(event_text, region):
    """مفتاح الحالة: الأصول المذكورة + المنطقة + تاريخ/دورة البيانات، أو بصمة النص لو ما في معرّفات."""
    t = event_text or ""
    ids = sorted(set(re.findall(r"\b(?:JOM\d{6}|MAC\d{6}|JO-[A-Z]{3}-CH\d{2})\b", t)))
    unit = re.findall(r"\bunit\s*(\d+)", t, re.I)
    ids += [f"unit{u}" for u in sorted(set(unit))]
    when = re.findall(r"\b(20\d\d-\d\d-\d\d)\b|cycle\s*(\d+)", t, re.I)
    when = sorted({a or f"c{b}" for a, b in when})
    if ids:
        raw = "|".join(ids) + "#" + (region or "") + "#" + ",".join(when)
    else:
        # بدون معرّفات (غالباً تقرير PDF): نص الاستخراج بيتغيّر صياغته كل مرة، فالبصمة
        # بتكون المنطقة + المجال (شحن/معدات/عدادات). الإعادة بتتحقق من الأدلة قبل أي اختصار.
        low = t.lower()
        if re.search(r"charg|\bev\b|charger|battery", low):
            dom = "charging"
        elif re.search(r"turbine|\bunit\b|temperature|plant|overheat", low):
            dom = "plant"
        elif re.search(r"meter|consumption|kwh", low):
            dom = "meter"
        else:
            dom = "txt:" + re.sub(r"\s+", " ", low).strip()
        raw = "dom#" + (region or "") + "#" + dom
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _tool_registry():
    from agents import meter_agent, plant_agent, charging_agent
    reg = {}
    for mod in (meter_agent, plant_agent, charging_agent):
        reg.update(mod.TOOLS)
    return reg


def _matches(old, new):
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    if ("error" in old) != ("error" in new):
        return False
    if bool(old.get("flagged")) != bool(new.get("flagged")) or bool(old.get("refutes")) != bool(new.get("refutes")):
        return False
    return abs(float(old.get("strength") or 0) - float(new.get("strength") or 0)) <= STRENGTH_TOL


def _approval_rejected(incident_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT status FROM approvals WHERE incident_id = ? ORDER BY id DESC LIMIT 1",
                           (incident_id,)).fetchone()
    finally:
        conn.close()
    return bool(row and row["status"] == "rejected")


def _legacy_key(event_text, region):
    """المفتاح القديم للنصوص بدون معرّفات (بصمة النص كامل) — عشان الدروس المحفوظة قبل ما تضيع."""
    norm = re.sub(r"\s+", " ", (event_text or "").lower()).strip()
    return hashlib.sha1(("txt#" + (region or "") + "#" + norm).encode("utf-8")).hexdigest()


def lookup(event_text, region):
    """يرجع الدرس لو موجود وصالح لإعادة الاستعمال، وإلا None."""
    key = case_key(event_text, region)
    conn = get_conn()
    try:
        _table(conn)
        # نفس النص بالحرف أولاً (أدق مطابقة)، وبعدين مفتاح المنطقة + المجال
        row = conn.execute("SELECT * FROM experience WHERE key = ?", (_legacy_key(event_text, region),)).fetchone()
        if row:
            key = row["key"]
        else:
            row = conn.execute("SELECT * FROM experience WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    if _approval_rejected(row["incident_id"]):
        return None   # إنسان رفض الاستنتاج السابق → ما منعيده
    try:
        return {"key": key, "incident_id": row["incident_id"],
                "results": json.loads(row["results_json"]), "challenges": json.loads(row["challenges_json"] or "[]")}
    except ValueError:
        return None


def replay(lesson, emit, pace=0.06):
    """
    بيعيد تشغيل الأدوات الحسابية للدرس (بدون LLM) وبيتأكد إنه الأدلة لسا نفسها.
    لو تطابقت: بيبث نفس أحداث التحقيق العادي (tool_call / tool_result / hypothesis)
    عشان الواجهة تشتغل طبيعي، وبيرجع results بنفس شكل dispatch(). لو لا: None.
    """
    from services.confidence import compute_confidence
    reg = _tool_registry()
    fresh = {}
    # 1) تحقق صامت أولاً — ما منبث شي قبل ما نتأكد إنه الخبرة صالحة
    for name, res in lesson["results"].items():
        if res.get("failed"):
            return None
        ev_new = []
        for ev in res.get("evidence", []):
            fn = reg.get(ev.get("tool"))
            if fn is None:
                return None
            try:
                out = fn(**(ev.get("input") or {}))
            except Exception:  # noqa: BLE001
                return None
            out = json.loads(json.dumps(out, default=_json_default))
            if not _matches(ev.get("output"), out):
                return None
            ev_new.append({"tool": ev["tool"], "input": ev.get("input") or {}, "output": out})
        fresh[name] = ev_new
    # 2) صالحة → نبث التحقيق بإيقاع سريع
    results = {}
    for name, res in lesson["results"].items():
        for ev in fresh[name]:
            emit({"agent": name, "type": "tool_call", "tool": ev["tool"], "args": ev["input"]})
            time.sleep(pace)
            emit({"agent": name, "type": "tool_result", "tool": ev["tool"], "result": ev["output"]})
            time.sleep(pace)
        conf = compute_confidence(fresh[name])
        emit({"agent": name, "type": "hypothesis", "text": res.get("hypothesis", ""), "confidence": conf})
        time.sleep(pace * 3)
        results[name] = {"agent": name, "hypothesis": res.get("hypothesis", ""), "confidence": conf, "evidence": fresh[name]}
    return results


def record(event_text, region, incident_id, results, debate, duration_s):
    """يحفظ/يحدّث الدرس بعد تحقيق كامل. التحقيقات يلي فيها وكيل فاشل ما بتنحفظ كخبرة."""
    if any(r.get("failed") for r in results.values()):
        return
    key = case_key(event_text, region)
    slim = {n: {"agent": n, "hypothesis": r.get("hypothesis", ""), "confidence": r.get("confidence"),
                "evidence": r.get("evidence", [])} for n, r in results.items()}
    chal = [{"by": c["by"], "against": c["against"], "challenged_tools": c.get("challenged_tools", []),
             "reason": c.get("reason", "")} for c in ((debate or {}).get("challenges") or [])
            if not str(c.get("reason", "")).startswith("(challenge call failed")]
    with _lock:
        conn = get_conn()
        try:
            _table(conn)
            conn.execute(
                "INSERT INTO experience (key, incident_id, results_json, challenges_json, duration_s) VALUES (?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET incident_id=excluded.incident_id, results_json=excluded.results_json, "
                "challenges_json=excluded.challenges_json, duration_s=excluded.duration_s, updated_at=CURRENT_TIMESTAMP",
                (key, incident_id, json.dumps(slim, default=_json_default, ensure_ascii=False),
                 json.dumps(chal, ensure_ascii=False), duration_s))
            conn.commit()
        finally:
            conn.close()


def mark_used(key):
    conn = get_conn()
    try:
        _table(conn)
        conn.execute("UPDATE experience SET uses = uses + 1 WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


# ---- إعادة استعمال تحديات المواجهة من الدرس (بدل نداء LLM) ----
_replay_local = threading.local()


def set_replay_challenges(challenges):
    q = {}
    for c in challenges or []:
        q.setdefault((c["by"], c["against"]), []).append(c)
    _replay_local.q = q


def clear_replay_challenges():
    _replay_local.q = None


def next_replay_challenge(by, against):
    q = getattr(_replay_local, "q", None)
    if not q:
        return None
    lst = q.get((by, against))
    return lst.pop(0) if lst else {"by": by, "against": against, "challenged_tools": [], "reason": "no valid objection"}
