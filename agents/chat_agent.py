"""
VOLT-AI — مساعد المحادثة العائم بواجهة مدار.

تصميم مقصود: هاد مش وكيل تحقيق رابع منافس (زي Meter/Plant/Charging/Geo)، هو طبقة
"تفسير + استعلام" فوق قرارات وبيانات موجودة أصلاً بقاعدة البيانات. ما بيولّد فرضيات
ولا ثقة جديدة، وما بيلمس منطق dispatch/compare_confidence/debate_round إطلاقاً —
هدفه يشرح للمستخدم شو صار وليش، ويقدر يستعلم عن أي رقم حقيقي بالقاعدة عبر أداة
query_database واحدة محدودة SELECT بس (زي نفس فلسفة المشروع: أرقام محسوبة/مسجّلة
حقيقية، مش كلام مولّد من الموديل).

اللغة: ما في اكتشاف لغة يدوي بالكود — الموديل نفسه مأمور بالـsystem prompt يرد
بنفس لغة آخر رسالة من المستخدم (عربي أو إنجليزي)، هيك القرار عند نفس المكان يلي
بيقرأ نية المستخدم فعلياً.
"""
import json
import re
import sqlite3

from groq import Groq

import config
from db.database import get_conn, get_incident, list_trace_events, get_geo_assessment

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


ALLOWED_TABLES = {
    "incidents", "hypotheses", "evidence", "arbitration_log", "approvals",
    "meters", "meter_readings", "agent_runs", "tool_calls", "trace_events",
    "actions", "jordan_chargers", "geo_assessments",
}


def _safe_select(sql):
    """يسمح فقط بجملة SELECT وحيدة وبدون أي أمر كتابة/DDL. يرفض أي شي غير هيك."""
    s = (sql or "").strip().rstrip(";").strip()
    if not s:
        return None, "empty query"
    if ";" in s:
        return None, "multiple statements are not allowed"
    if not re.match(r"(?is)^\s*select\b", s):
        return None, "only SELECT statements are allowed"
    if re.search(r"(?is)\b(insert|update|delete|drop|alter|attach|detach|pragma|create|replace|vacuum)\b", s):
        return None, "write/DDL statements are not allowed"
    return s, None


def query_database(sql):
    s, err = _safe_select(sql)
    if err:
        return {"error": err}
    if "limit" not in s.lower():
        s = s + " LIMIT 25"
    conn = get_conn()
    try:
        rows = conn.execute(s).fetchall()
        return {"rows": [dict(r) for r in rows]}
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()


TOOL_SPEC = [{
    "type": "function",
    "function": {
        "name": "query_database",
        "description": (
            "Run a single read-only SQL SELECT query against the Madar Energy database. "
            "Tables available: incidents, hypotheses, evidence, arbitration_log, approvals, "
            "meters, meter_readings, agent_runs, tool_calls, trace_events, actions, "
            "jordan_chargers, geo_assessments. Only SELECT is allowed (no INSERT/UPDATE/"
            "DELETE/DDL). Results are capped at 25 rows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT statement."},
            },
            "required": ["sql"],
        },
    },
}]

SYSTEM_PROMPT = """You are VOLT-AI, the small floating assistant embedded in the Madar Energy \
control room web app (a live multi-agent incident investigation system for Jordan's power grid).

Ground rules:
- You are NOT a 4th competing investigation agent. Meter Agent, Plant Agent, Charging Agent and \
the Geospatial Risk Agent already produced whatever hypothesis/confidence/decision exists. You \
explain, summarize and query that existing data — you never invent a new hypothesis or a new \
confidence score of your own.
- ALWAYS reply in the same language the user's latest message is written in (Arabic or English). \
Mirror it every turn. Never ask which language to use, never mix languages in one reply.
- Keep answers short, concrete and grounded. When you cite a number, region, agent name or date, \
it must come from the incident context given to you or from a query_database call — never guess \
a specific figure.
- You have one tool, query_database(sql): a single read-only SELECT. Use it when the user asks \
something the given incident context doesn't already contain (stats across incidents, other \
regions, historical comparisons, etc). Don't call it for things you already have in context.
- If asked about something entirely unrelated to Madar Energy / this incident / the Jordanian \
energy grid, answer briefly and steer back to what you can actually help with here.
"""


def _incident_context(incident_id):
    incident = get_incident(incident_id)
    if not incident:
        return None
    events = list_trace_events(incident_id)
    # نلقّط الأحداث المفيدة بس (فرضيات + نتائج نقاش)، منعشان ما نغرق الموديل بسجل كامل
    # — وقلّلنا الحد الأقصى من 40 لـ15 عشان نخفف حجم الطلب ونسرّع الرد (كانت بطيئة)
    useful = [e for e in events if e.get("event_type") in
              ("hypothesis", "debate_result", "final_decision", "challenge")]
    geo = get_geo_assessment(incident_id)
    return {
        "incident": incident,
        "key_events": useful[-15:],
        "geo_assessment": geo,
    }


def chat(message, incident_id=None, history=None):
    """يرجع نص الرد (str). يرمي استثناء لو صار خطأ حقيقي بالاتصال بـGroq — الكولر مسؤول يلتقطه."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if incident_id:
        ctx = _incident_context(incident_id)
        if ctx:
            messages.append({
                "role": "system",
                "content": "Current open incident context (JSON, from the real database):\n"
                           + json.dumps(ctx, default=str)[:3500],
            })

    # طول الطلب (context + history) هو أكبر سبب للبطء — قلّلناه هون: آخر 4 أدوار
    # بدل 8، وسقف نص أقصر للرسالة الواحدة، عشان الرد يرجع أسرع
    for turn in (history or [])[-4:]:
        role = "user" if turn.get("role") == "user" else "assistant"
        content = str(turn.get("content", ""))[:1200]
        if content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": (message or "")[:2000]})

    client = _get_client()
    for _ in range(3):
        response = client.chat.completions.create(
            model=config.MODEL, messages=messages, tools=TOOL_SPEC,
            tool_choice="auto", temperature=0.3,
        )
        msg = response.choices[0].message
        if not msg.tool_calls:
            return (msg.content or "").strip() or "..."

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if tc.function.name == "query_database":
                result = query_database(args.get("sql", ""))
            else:
                result = {"error": f"unknown tool {tc.function.name}"}
            messages.append({
                "role": "tool", "tool_call_id": tc.id,
                "content": json.dumps(result, default=str)[:2500],
            })

    # وصلنا لحد المحاولات بدون جواب نهائي — نجبره يجاوب بدون أدوات
    messages.append({"role": "user", "content": "Stop calling tools now and answer with what you have."})
    response = client.chat.completions.create(
        model=config.MODEL, messages=messages, tool_choice="none", temperature=0.3,
    )
    return (response.choices[0].message.content or "").strip() or "..."


def generate_title(message):
    """
    عنوان قصير (2-5 كلمات) للمحادثة الجديدة بالشريط الجانبي، بنفس لغة السؤال —
    بدل ما نقص أول رسالة حرفياً. Best effort: أي خطأ هون مش حرج، الواجهة بترجع
    تلقائياً لأول جزء من رسالة المستخدم لو رجعنا None.
    """
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=config.MODEL,
            messages=[
                {"role": "system", "content": (
                    "Generate a very short chat title (2-5 words, same language as the "
                    "user's message, no quotes, no trailing punctuation) that summarizes "
                    "what this message is about. Reply with ONLY the title, nothing else."
                )},
                {"role": "user", "content": (message or "")[:300]},
            ],
            tool_choice="none", temperature=0.3, max_tokens=20,
        )
        title = (response.choices[0].message.content or "").strip().strip('"').strip("'")
        return title[:40] or None
    except Exception:
        return None
