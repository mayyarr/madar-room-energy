-- الحدث الأساسي: كل مشكلة غامضة تدخل النظام تصير سطراً هنا
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    winner_agent TEXT,
    final_confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- فرضية كل وكيل عن الحدث
CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    agent TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (incident_id) REFERENCES incidents(id)
);

-- الأدلة: كل استدعاء أداة ونتيجته
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    tool_input TEXT,
    tool_output TEXT,
    weight REAL DEFAULT 1.0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id)
);

-- سجل التحكيم: كل خطوة في المقارنة والنقاش
CREATE TABLE IF NOT EXISTS arbitration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    round INTEGER DEFAULT 1,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (incident_id) REFERENCES incidents(id)
);

-- الموافقات البشرية على الإجراءات المؤثرة
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    FOREIGN KEY (incident_id) REFERENCES incidents(id)
);
-- العدادات: كل عداد مربوط بمنطقة أردنية
CREATE TABLE IF NOT EXISTS meters (
    meter_id TEXT PRIMARY KEY,
    region TEXT NOT NULL
);

-- القراءات اليومية لكل عداد
CREATE TABLE IF NOT EXISTS meter_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id TEXT NOT NULL,
    day TEXT NOT NULL,
    kwh_total REAL,
    kwh_mean REAL,
    kwh_max REAL,
    kwh_min REAL,
    kwh_std REAL,
    readings_count INTEGER,
    FOREIGN KEY (meter_id) REFERENCES meters(meter_id)
);

CREATE INDEX IF NOT EXISTS idx_readings_meter_day
    ON meter_readings (meter_id, day);
-- ===== الإصدار 2 (P3 - طبقة الحفظ) =====
-- تشغيلة كاملة لوكيل واحد على حدث واحد: من بداية الاستدعاء لنهايته
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    agent TEXT NOT NULL,
    round INTEGER DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'running',   -- running | success | error
    hypothesis_id INTEGER,                     -- يترابط بعد ما تنحفظ الفرضية
    error_message TEXT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    FOREIGN KEY (incident_id) REFERENCES incidents(id),
    FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id)
);

-- كل استدعاء أداة خام كما صدر عن الوكيل (سجل تتبع منفصل عن evidence)
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_run_id INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    tool_input TEXT,
    tool_output TEXT,
    forced INTEGER DEFAULT 0,   -- 1 إذا الكود فرضه ولم يختره الموديل
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_run_id) REFERENCES agent_runs(id)
);

-- كل حدث بث (emit) كما يصل للواجهة الحية، محفوظ حتى لو ما كان أحد يشاهد
CREATE TABLE IF NOT EXISTS trace_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    agent TEXT,
    event_type TEXT NOT NULL,
    payload TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (incident_id) REFERENCES incidents(id)
);

-- الإجراء المقترح على الحدث (اللي بينتظر موافقة بشرية عبر approvals)
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    description TEXT,
    risk_level TEXT DEFAULT 'medium',   -- low | medium | high
    status TEXT NOT NULL DEFAULT 'proposed',   -- proposed | approved | rejected
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (incident_id) REFERENCES incidents(id)
);
