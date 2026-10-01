import sqlite3
import os
import sys
import json

# نضيف المجلد الرئيسي حتى نقدر نستورد config من أي مكان
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

# ===== أعمدة جديدة على جداول قديمة (P3) =====
# كل قيمة: (اسم_العمود, تعريف SQL). نتأكد قبل الإضافة أنها غير موجودة أصلاً.
MIGRATIONS = {
    "incidents": [
        ("severity", "TEXT"),
        ("region", "TEXT"),
        ("event_time", "TEXT"),
        ("resolved_at", "TEXT"),
        ("financial_impact", "REAL"),
    ],
    "hypotheses": [
        ("round", "INTEGER DEFAULT 1"),
        ("status", "TEXT DEFAULT 'active'"),   # active | rejected | winner | undecided
        ("rejection_reason", "TEXT"),
        ("confidence_detail", "TEXT"),          # JSON: تفاصيل حساب confidence.py
    ],
    "approvals": [
        ("action_id", "INTEGER"),
        ("risk_level", "TEXT"),
        ("decided_by", "TEXT"),
        ("comment", "TEXT"),
    ],
    "arbitration_log": [
        ("challenger_agent", "TEXT"),
        ("challenged_agent", "TEXT"),
        ("confidence_before", "TEXT"),   # JSON: {"Meter Agent": 0.6, ...}
        ("confidence_after", "TEXT"),
    ],
}


def get_conn():
    """يفتح اتصالاً بالقاعدة. الصفوف ترجع كقواميس بدل tuples."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _existing_columns(conn, table):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def migrate_schema():
    """يضيف الأعمدة الناقصة على الجداول القديمة، بدون ما يلمس الموجود.
    آمنة تماماً نشغلها أكتر من مرة (بتتحقق من كل عمود قبل ما تضيفه)."""
    conn = get_conn()
    added = []
    for table, columns in MIGRATIONS.items():
        existing = _existing_columns(conn, table)
        for col_name, col_def in columns:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
                added.append(f"{table}.{col_name}")
    conn.commit()
    conn.close()
    return added


def init_db():
    """ينشئ الجداول من schema.sql إن لم تكن موجودة، ثم يشغل الـ migration."""
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = f.read()
    conn = get_conn()
    conn.executescript(schema)
    conn.commit()
    conn.close()
    added = migrate_schema()
    return added


# ===== incidents =====

def insert_incident(title, description="", severity=None, region=None, event_time=None):
    """يضيف حدثاً جديداً ويرجع رقمه."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO incidents (title, description, severity, region, event_time) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, description, severity, region, event_time),
    )
    conn.commit()
    incident_id = cur.lastrowid
    conn.close()
    return incident_id


def get_incident(incident_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_incidents():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM incidents ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def close_incident(incident_id, status, winner_agent=None, final_confidence=None,
                    financial_impact=None):
    """يقفل الحدث بحالة نهائية: resolved أو undecided مثلاً."""
    conn = get_conn()
    conn.execute(
        "UPDATE incidents SET status = ?, winner_agent = ?, final_confidence = ?, "
        "financial_impact = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, winner_agent, final_confidence, financial_impact, incident_id),
    )
    conn.commit()
    conn.close()


# ===== geo_assessments (Geospatial Risk & Impact Agent) =====
# جدوله الخاص، يُنشأ هون مباشرة (نفس نمط seed_ocm.py create_table)، ما يلمس schema.sql.

def _create_geo_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geo_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id INTEGER NOT NULL,
            region TEXT,
            stress_pct REAL,
            charging_points INTEGER,
            network_avg_points REAL,
            cascading_json TEXT,
            burn_daily_lo REAL,
            burn_daily_hi REAL,
            burn_per_second REAL,
            response_system_seconds REAL,
            response_baseline_seconds REAL,
            speedup_factor REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (incident_id) REFERENCES incidents(id)
        )
    """)
    conn.commit()


def save_geo_assessment(incident_id, region=None, stress=None, cascading=None,
                         burn_rate=None, response=None):
    """يحفظ خرج agents/geo_agent.py كاملاً بضربة واحدة. أي جزء غير متوفر يُترك NULL."""
    conn = get_conn()
    _create_geo_table(conn)
    conn.execute(
        "INSERT INTO geo_assessments (incident_id, region, stress_pct, charging_points, "
        "network_avg_points, cascading_json, burn_daily_lo, burn_daily_hi, burn_per_second, "
        "response_system_seconds, response_baseline_seconds, speedup_factor) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            incident_id, region,
            (stress or {}).get("stress_pct"), (stress or {}).get("charging_points"),
            (stress or {}).get("network_avg_points"),
            json.dumps((cascading or {}).get("at_risk", []), ensure_ascii=False),
            (burn_rate or {}).get("daily_lo_jod"), (burn_rate or {}).get("daily_hi_jod"),
            (burn_rate or {}).get("per_second_jod"),
            (response or {}).get("system_seconds"), (response or {}).get("baseline_seconds"),
            (response or {}).get("speedup_factor"),
        ),
    )
    conn.commit()
    geo_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return geo_id


def get_geo_assessment(incident_id):
    conn = get_conn()
    _create_geo_table(conn)
    row = conn.execute(
        "SELECT * FROM geo_assessments WHERE incident_id = ? ORDER BY id DESC LIMIT 1",
        (incident_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["cascading"] = json.loads(d.pop("cascading_json") or "[]")
    return d


# ===== VOLT-AI: محادثات + ملاحظات (نفس نمط geo_assessments — جدول ذاتي الإنشاء) =====

def _create_chat_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)


def create_conversation(title=None):
    conn = get_conn(); _create_chat_tables(conn)
    cur = conn.execute("INSERT INTO chat_conversations (title) VALUES (?)", (title,))
    conn.commit(); cid = cur.lastrowid; conn.close()
    return cid


def list_conversations():
    conn = get_conn(); _create_chat_tables(conn)
    rows = conn.execute("SELECT * FROM chat_conversations ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def rename_conversation(conversation_id, title):
    if not title:
        return
    conn = get_conn(); _create_chat_tables(conn)
    conn.execute("UPDATE chat_conversations SET title = ? WHERE id = ?", (title, conversation_id))
    conn.commit(); conn.close()


def delete_conversation(conversation_id):
    conn = get_conn(); _create_chat_tables(conn)
    conn.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conversation_id,))
    conn.execute("DELETE FROM chat_conversations WHERE id = ?", (conversation_id,))
    conn.commit(); conn.close()


def add_chat_message(conversation_id, role, content):
    conn = get_conn(); _create_chat_tables(conn)
    conn.execute(
        "INSERT INTO chat_messages (conversation_id, role, content) VALUES (?, ?, ?)",
        (conversation_id, role, content),
    )
    conn.commit(); conn.close()


def list_chat_messages(conversation_id):
    conn = get_conn(); _create_chat_tables(conn)
    rows = conn.execute(
        "SELECT * FROM chat_messages WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_note(content=""):
    conn = get_conn(); _create_chat_tables(conn)
    cur = conn.execute("INSERT INTO chat_notes (content) VALUES (?)", (content,))
    conn.commit(); nid = cur.lastrowid; conn.close()
    return nid


def list_notes():
    conn = get_conn(); _create_chat_tables(conn)
    rows = conn.execute("SELECT * FROM chat_notes ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_note(note_id, content):
    conn = get_conn(); _create_chat_tables(conn)
    conn.execute(
        "UPDATE chat_notes SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (content, note_id),
    )
    conn.commit(); conn.close()


def delete_note(note_id):
    conn = get_conn(); _create_chat_tables(conn)
    conn.execute("DELETE FROM chat_notes WHERE id = ?", (note_id,))
    conn.commit(); conn.close()


# ===== agent_runs =====

def start_agent_run(incident_id, agent, round=1):
    """يفتح سجل تشغيلة جديدة لوكيل على حدث، يرجع agent_run_id."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO agent_runs (incident_id, agent, round, status) "
        "VALUES (?, ?, ?, 'running')",
        (incident_id, agent, round),
    )
    conn.commit()
    run_id = cur.lastrowid
    conn.close()
    return run_id


def finish_agent_run(agent_run_id, status, hypothesis_id=None, error_message=None):
    """يقفل سجل التشغيلة: status = success أو error."""
    conn = get_conn()
    conn.execute(
        "UPDATE agent_runs SET status = ?, hypothesis_id = ?, error_message = ?, "
        "finished_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, hypothesis_id, error_message, agent_run_id),
    )
    conn.commit()
    conn.close()


# ===== tool_calls (سجل خام) + evidence (مرتبط بالفرضية، حسب العقد الأصلي) =====

def insert_tool_call(agent_run_id, tool_name, tool_input, tool_output, forced=False):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO tool_calls (agent_run_id, tool_name, tool_input, tool_output, forced) "
        "VALUES (?, ?, ?, ?, ?)",
        (agent_run_id, tool_name, json.dumps(tool_input, ensure_ascii=False),
         json.dumps(tool_output, ensure_ascii=False), int(bool(forced))),
    )
    conn.commit()
    call_id = cur.lastrowid
    conn.close()
    return call_id


def insert_hypothesis(incident_id, agent, hypothesis, confidence, round=1,
                       status="active", confidence_detail=None):
    """يحفظ فرضية وكيل ويرجع hypothesis_id."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO hypotheses (incident_id, agent, hypothesis, confidence, round, "
        "status, confidence_detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (incident_id, agent, hypothesis, confidence, round, status,
         json.dumps(confidence_detail, ensure_ascii=False) if confidence_detail else None),
    )
    conn.commit()
    hyp_id = cur.lastrowid
    conn.close()
    return hyp_id


def update_hypothesis_status(hypothesis_id, status, rejection_reason=None):
    """يحدث حالة فرضية بعد التحكيم: winner / rejected / undecided."""
    conn = get_conn()
    conn.execute(
        "UPDATE hypotheses SET status = ?, rejection_reason = ? WHERE id = ?",
        (status, rejection_reason, hypothesis_id),
    )
    conn.commit()
    conn.close()

def insert_evidence(hypothesis_id, tool_name, tool_input, tool_output, weight=None):
    """يحفظ دليلاً واحداً مرتبطاً بفرضية (حسب العقد بين الوكلاء).
    إذا لم يُحدَّد weight، نأخذه تلقائياً من strength بمخرجات الأداة."""
    if weight is None:
        weight = float(tool_output.get("strength", 1.0)) if isinstance(tool_output, dict) else 1.0
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO evidence (hypothesis_id, tool_name, tool_input, tool_output, weight) "
        "VALUES (?, ?, ?, ?, ?)",
        (hypothesis_id, tool_name, json.dumps(tool_input, ensure_ascii=False),
         json.dumps(tool_output, ensure_ascii=False), weight),
    )
    conn.commit()
    ev_id = cur.lastrowid
    conn.close()
    return ev_id

def save_agent_result(incident_id, agent_run_id, result, round=1):
    """يحفظ خرج agent.run() كاملاً دفعة وحدة: الفرضية + كل الأدلة.
    result بالشكل المتفق عليه بالعقد:
    {"agent", "hypothesis", "confidence", "evidence": [{"tool","input","output"}]}
    يرجع hypothesis_id."""
    hyp_id = insert_hypothesis(
        incident_id, result["agent"], result["hypothesis"], result["confidence"],
        round=round,
    )
    for ev in result.get("evidence", []):
        insert_evidence(hyp_id, ev.get("tool"), ev.get("input"), ev.get("output"))
    finish_agent_run(agent_run_id, "success", hypothesis_id=hyp_id)
    return hyp_id


# ===== trace_events (نسخة محفوظة من كل ما يبثه emit) =====

def insert_trace_event(incident_id, agent, event_type, payload=None):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO trace_events (incident_id, agent, event_type, payload) "
        "VALUES (?, ?, ?, ?)",
        (incident_id, agent, event_type,
         json.dumps(payload, ensure_ascii=False) if payload is not None else None),
    )
    conn.commit()
    ev_id = cur.lastrowid
    conn.close()
    return ev_id


def list_trace_events(incident_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trace_events WHERE incident_id = ? ORDER BY id ASC",
        (incident_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ===== arbitration_log =====

def insert_arbitration_log(incident_id, event_type, message, round=1,
                            challenger_agent=None, challenged_agent=None,
                            confidence_before=None, confidence_after=None):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO arbitration_log (incident_id, round, event_type, message, "
        "challenger_agent, challenged_agent, confidence_before, confidence_after) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (incident_id, round, event_type, message, challenger_agent, challenged_agent,
         json.dumps(confidence_before, ensure_ascii=False) if confidence_before else None,
         json.dumps(confidence_after, ensure_ascii=False) if confidence_after else None),
    )
    conn.commit()
    log_id = cur.lastrowid
    conn.close()
    return log_id


def list_arbitration_log(incident_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM arbitration_log WHERE incident_id = ? ORDER BY id ASC",
        (incident_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ===== actions + approvals (الإجراء المقترح + الموافقة البشرية عليه) =====

def insert_action(incident_id, action_type, description, risk_level="medium"):
    """يقترح إجراءً على حدث، يرجع action_id. الحالة تبلش proposed."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO actions (incident_id, action_type, description, risk_level) "
        "VALUES (?, ?, ?, ?)",
        (incident_id, action_type, description, risk_level),
    )
    conn.commit()
    action_id = cur.lastrowid
    conn.close()
    return action_id


def request_approval(incident_id, action_id, action_text, risk_level="medium"):
    """يفتح طلب موافقة بشرية على إجراء معين، يرجع approval_id."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO approvals (incident_id, action, action_id, risk_level) "
        "VALUES (?, ?, ?, ?)",
        (incident_id, action_text, action_id, risk_level),
    )
    conn.commit()
    approval_id = cur.lastrowid
    conn.close()
    return approval_id


def decide_approval(approval_id, status, decided_by=None, comment=None):
    """يسجل قرار الإنسان: status = approved أو rejected."""
    conn = get_conn()
    conn.execute(
        "UPDATE approvals SET status = ?, decided_by = ?, comment = ?, "
        "decided_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, decided_by, comment, approval_id),
    )
    if status in ("approved", "rejected"):
        row = conn.execute(
            "SELECT action_id FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        if row and row["action_id"]:
            new_action_status = "approved" if status == "approved" else "rejected"
            conn.execute(
                "UPDATE actions SET status = ? WHERE id = ?",
                (new_action_status, row["action_id"]),
            )
    conn.commit()
    conn.close()


def list_pending_approvals(incident_id=None):
    """
    نضم incidents (لجلب الثقة النهائية الحقيقية final_confidence والوكيل الفائز) وactions
    (لجلب نوع الإجراء)، عشان الواجهة تحسب الأثر المالي المقترح تناسبياً مع ثقة القرار الفعلية
    مش برقم ثابت حسب فئة الخطورة (P9.4).
    """
    conn = get_conn()
    base_sql = (
        "SELECT a.*, i.final_confidence AS final_confidence, i.winner_agent AS winner_agent, "
        "i.region AS region, act.action_type AS action_type "
        "FROM approvals a "
        "LEFT JOIN incidents i ON i.id = a.incident_id "
        "LEFT JOIN actions act ON act.id = a.action_id "
        "WHERE a.status = 'pending'"
    )
    if incident_id:
        rows = conn.execute(base_sql + " AND a.incident_id = ? ORDER BY a.id ASC", (incident_id,)).fetchall()
    else:
        rows = conn.execute(base_sql + " ORDER BY a.id ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    added = init_db()
    print("Database created/updated at:", config.DB_PATH)
    if added:
        print("Columns added by migration:", added)
    else:
        print("No migration needed (schema already up to date).")
