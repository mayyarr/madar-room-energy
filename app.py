"""
app.py - خادم Flask الرئيسي (P6.1)

المسارات:
- GET  /                      : الصفحة الرئيسية (غرفة التحكم)
- POST /api/trigger           : يبدأ تحقيق جديد (يشغل run_and_save بخيط منفصل)
- GET  /api/stream            : اتصال SSE، يبث كل الأحداث الحية للمتصفح
- GET  /api/incidents         : قائمة كل الحوادث (سجل الحالات)
- GET  /api/incidents/<id>    : تفاصيل حادثة واحدة كاملة (محضر الجلسة)
- GET  /api/approvals         : الموافقات المعلّقة
- POST /api/approvals/<id>/approve
- POST /api/approvals/<id>/reject
"""
import os
import sys
import json
import threading
import base64

from flask import Flask, Response, render_template, request, jsonify, stream_with_context

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from services import event_bus  # noqa: E402
from services import approvals  # noqa: E402
from services import chaos  # noqa: E402
from services import auto_sim  # noqa: E402
from agents import synthetic_agent  # noqa: E402
from services import report_intake  # noqa: E402
from services import mailer  # noqa: E402
from services import grid_graph  # noqa: E402
from db import database as db  # noqa: E402
from agents.arbitrator import run_and_save  # noqa: E402
from agents.chat_agent import chat as volt_chat, generate_title as volt_title  # noqa: E402

app = Flask(__name__)

# ثلاثة سيناريوهات جاهزة (زر "محاكاة حدث" بالواجهة) — واحد لكل وكيل مختص، بنتائج
# نهائية مختلفة قصداً (فوز واضح / تعادل ومواجهة حية / فوز واضح من وكيل تاني) عشان
# العرض قدام اللجنة يبيّن تنوع حقيقي بمجالات المشروع الثلاثة، مش بس العدادات.
# كل رقم هون حقيقي ومختبَر فعلياً (شوف context/PROJECT_CONTEXT.md وCHANGELOG.md) —
# ما في نتيجة "مطبوخة"، فقط اختيار أي جزء من الداتا الحقيقية الموجودة نعرضه.
PRESET_SCENARIOS = {
    "clear": {
        "title": "هبوط استهلاك مفاجئ - MAC002068",
        "event_text": (
            "Meter MAC002068 in Amman region dropped sharply from 22.07 to 9.82 "
            "kWh/day, ratio to neighbors fell from 1.28x to 0.53x. Investigate the cause."
        ),
        "region": "Amman",
    },
    "tie": {
        "title": "ارتفاع استهلاك مفاجئ - MAC000689",
        "event_text": (
            "Meter MAC000689 in Madaba region rose from 22.63 to 33.97 kWh/day "
            "as of 2020-02-14. Investigate the cause."
        ),
        "region": "Madaba",
    },
    "plant": {
        "title": "ارتفاع حرارة معدّات - Unit 1 (العقبة)",
        "event_text": (
            "Incident: unit 1 shows rising temperatures and the operators are worried. "
            "Investigate the unit as of cycle 187."
        ),
        "region": "Aqaba",
    },
}

# مواجهة الثلاثة صارت سلوك افتراضي لكل تحقيق — ثابت أو حر — مش زر خاص منفصل.
# كل حادثة (أياً كان مصدرها) بتفوت الثلاثة وكلاء بجولة تحدٍّ جماعية أول شي؛
# الأضعف بيتقصى صراحة، والاتنين الباقيين يكملوا عادي (شوف debate_round بـ
# agents/arbitrator.py). هيك "الفوز الواضح" نفسه صار مرحلة أخيرة من مواجهة
# حقيقية بالثلاثة، مش تخطّي للمواجهة بالكامل.
FORCE_THREE_WAY_ALWAYS = True


def _run_incident_in_background(event_text, title, region):
    """
    تشغّل التحقيق الكامل بخيط منفصل عشان الطلب (request) يرجع فوراً للمتصفح،
    وكل حدث يصير أثناء التحقيق (tool_call, hypothesis, challenge, debate_result...)
    ينبث لحظياً عبر event_bus.publish -> SSE، بدل ما ينتظر المتصفح لين يخلص كل شي.
    """
    try:
        event_bus.publish({"type": "incident_started", "title": title})
        result = run_and_save(
            event_text, title=title, region=region,
            emit=event_bus.publish, save_trace=True,
            force_three_way=FORCE_THREE_WAY_ALWAYS,
        )
        # حدث ختامي صريح للواجهة (run_and_save نفسها ما تبث incident_id تلقائياً)
        event_bus.publish({
            "type": "incident_saved",
            "incident_id": result["incident_id"],
            "decision": result["decision"]["status"],
            "debate": result["debate"]["status"] if result["debate"] else None,
        })
    except Exception as e:
        event_bus.publish({"type": "incident_error", "error": str(e)})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/trigger", methods=["POST"])
def trigger():
    """
    يبدأ تحقيق جديد. يقبل إما preset (من PRESET_SCENARIOS) أو نص حر (event_text).
    ما بينتظر التحقيق يخلص — بيرجع فوراً، والمتصفح بيتابع عبر /api/stream.
    """
    data = request.get_json(force=True, silent=True) or {}
    preset = data.get("preset")

    if preset and preset in PRESET_SCENARIOS:
        scenario = PRESET_SCENARIOS[preset]
        event_text = scenario["event_text"]
        title = scenario["title"]
        region = scenario["region"]
    else:
        event_text = (data.get("event_text") or "").strip()
        if not event_text:
            return jsonify({"error": "event_text is required (or a valid preset)"}), 400
        title = data.get("title") or event_text[:80]
        region = data.get("region")

    thread = threading.Thread(
        target=_run_incident_in_background,
        args=(event_text, title, region),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "started", "title": title})


@app.route("/api/stream")
def stream():
    """اتصال SSE دائم. كل متصفح فاتح هاد المسار بيستقبل كل event ينبث لحظياً."""
    def gen():
        q = event_bus.subscribe()
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                event = q.get()  # يستنى لحد ما يجي event جديد (بدون polling)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            event_bus.unsubscribe(q)

    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/incidents")
def list_incidents():
    return jsonify(db.list_incidents())


@app.route("/api/incidents/<int:incident_id>")
def incident_detail(incident_id):
    """تفاصيل حادثة كاملة: الحادثة نفسها + كل الأحداث + سجل التحكيم (لمحضر الجلسة)."""
    incident = db.get_incident(incident_id)
    if incident is None:
        return jsonify({"error": "not found"}), 404
    conn = db.get_conn()
    actions = [dict(r) for r in conn.execute("SELECT * FROM actions WHERE incident_id = ? ORDER BY id", (incident_id,))]
    appr = [dict(r) for r in conn.execute("SELECT * FROM approvals WHERE incident_id = ? ORDER BY id", (incident_id,))]
    conn.close()
    return jsonify({
        "incident": incident,
        "actions": actions,
        "approvals": appr,
        "geo": db.get_geo_assessment(incident_id),
        "trace_events": db.list_trace_events(incident_id),
        "arbitration_log": db.list_arbitration_log(incident_id),
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    VOLT-AI: نقطة نهاية محادثة واحدة — مو وكيل تحقيق جديد، طبقة تفسير/استعلام فوق
    البيانات الموجودة أصلاً (شوف agents/chat_agent.py للتفاصيل والحدود).
    """
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400
    incident_id = data.get("incident_id")
    history = data.get("history") or []
    conversation_id = data.get("conversation_id")
    try:
        reply = volt_chat(message, incident_id=incident_id, history=history)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    result = {"reply": reply}
    # أول سؤال حقيقي بمحادثة جديدة (الواجهة بتحدد هيك، مش من طول history لأنه دايماً
    # في رسالة ترحيب بالأول) → نولّد عنوان قصير للشريط الجانبي بدل ما نقص السؤال حرفياً
    if data.get("need_title"):
        result["title"] = volt_title(message)
    # نحفظ المحادثة فعلياً بقاعدة البيانات (مو بس localStorage) عشان الحذف يمسحها
    # نهائياً مش بس من المتصفح
    if conversation_id:
        db.add_chat_message(conversation_id, "user", message)
        db.add_chat_message(conversation_id, "assistant", reply)
        if result.get("title"):
            db.rename_conversation(conversation_id, result["title"])
    return jsonify(result)


@app.route("/api/chat/conversations", methods=["GET", "POST"])
def chat_conversations():
    """VOLT-AI: قائمة المحادثات المحفوظة (GET) أو إنشاء وحدة جديدة (POST)."""
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        cid = db.create_conversation(title=data.get("title"))
        return jsonify({"id": cid})
    return jsonify(db.list_conversations())


@app.route("/api/chat/conversations/<int:conversation_id>", methods=["GET", "DELETE"])
def chat_conversation_detail(conversation_id):
    """VOLT-AI: رسائل محادثة محفوظة (GET)، أو حذفها نهائياً من قاعدة البيانات (DELETE)."""
    if request.method == "DELETE":
        db.delete_conversation(conversation_id)
        return jsonify({"status": "deleted"})
    return jsonify({"messages": db.list_chat_messages(conversation_id)})


@app.route("/api/notes", methods=["GET", "POST"])
def notes_collection():
    """VOLT-AI: قائمة الملاحظات المحفوظة (GET) أو إضافة ملاحظة جديدة (POST)."""
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        nid = db.create_note((data.get("content") or "").strip())
        return jsonify({"id": nid})
    return jsonify(db.list_notes())


@app.route("/api/notes/<int:note_id>", methods=["PUT", "DELETE"])
def notes_detail(note_id):
    """VOLT-AI: تعديل ملاحظة محفوظة (PUT) أو حذفها نهائياً (DELETE)."""
    if request.method == "DELETE":
        db.delete_note(note_id)
        return jsonify({"status": "deleted"})
    data = request.get_json(force=True, silent=True) or {}
    db.update_note(note_id, (data.get("content") or "").strip())
    return jsonify({"status": "ok"})


@app.route("/api/approvals")
def list_approvals():
    incident_id = request.args.get("incident_id", type=int)
    return jsonify(approvals.get_pending(incident_id))


@app.route("/api/chaos/arm", methods=["POST"])
def chaos_arm():
    """
    وضع الفوضى (P9.3، للعرض الحي فقط): يسلّح فشل متعمّد لوكيل واحد (محدد أو عشوائي)
    بأول استدعاء أداة بالتشغيلة الجاية، عشان نثبت للجنة إنه فشل وكيل واحد ما بيكسر
    التحقيق كامل (dispatch/_run_one بيلتقطونه ويكمل الوكلاء الباقيين).
    """
    data = request.get_json(force=True, silent=True) or {}
    agent = data.get("agent")
    armed = chaos.arm(agent)
    event_bus.publish({"type": "chaos_armed", "agent": armed})
    return jsonify({"status": "armed", "agent": armed})


@app.route("/api/auto/start", methods=["POST"])
def auto_start():
    """
    زر "تشغيل تلقائي": ساعة محاكاة بتفحص الداتا الحقيقية يوم بيوم، وأول ما تكتشف
    شذوذ بتفتح تحقيق كامل لحالها (نفس مسار /api/trigger بالضبط). جولة وحدة لكل ضغطة.
    """
    ok = auto_sim.start(_run_incident_in_background, event_bus.publish)
    if not ok:
        return jsonify({"status": "busy"}), 409
    return jsonify({"status": "started"})


@app.route("/api/auto/stop", methods=["POST"])
def auto_stop():
    return jsonify(auto_sim.stop())


@app.route("/api/auto/status")
def auto_status():
    return jsonify(auto_sim.status())


@app.route("/api/synthetic")
def synthetic_list():
    """وكيل الحوادث الاصطناعية: قائمة الحوادث الأردنية المحقونة (بدون مفتاح الإجابة)."""
    return jsonify(synthetic_agent.public_list())


@app.route("/api/synthetic/<scenario_id>/verify")
def synthetic_verify(scenario_id):
    """فحص بدون Groq إنه العطل المحقون موجود فعلاً بالداتا."""
    return jsonify(synthetic_agent.verify(scenario_id))


@app.route("/api/synthetic/<scenario_id>/run", methods=["POST"])
def synthetic_run(scenario_id):
    res = synthetic_agent.start(scenario_id, event_bus.publish)
    if res == "busy":
        return jsonify({"status": "busy"}), 409
    if res == "unknown":
        return jsonify({"error": "unknown scenario"}), 404
    return jsonify({"status": "started"})


@app.route("/api/report", methods=["POST"])
def upload_report():
    """
    زر "+": يستقبل تقرير PDF، النظام بيستخرج منه الحادثة لحاله (بدون تشخيص)،
    وبعدها بيبلش التحقيق العادي (الوكلاء + جولة التحدي) تلقائياً.
    """
    f = request.files.get("file")
    if not f or not (f.filename or "").lower().endswith(".pdf"):
        return jsonify({"error": "لازم ملف PDF"}), 400
    data = f.read()
    if len(data) > 8 * 1024 * 1024:
        return jsonify({"error": "الملف أكبر من 8MB"}), 400
    filename = f.filename

    def work():
        event_bus.publish({"type": "report_received", "filename": filename})
        try:
            r = report_intake.process(data)
        except Exception as e:  # noqa: BLE001
            event_bus.publish({"type": "report_error", "filename": filename, "error": str(e)})
            return
        event_bus.publish({"type": "report_parsed", "filename": filename,
                           "fields": r["fields"], "event_text": r["event_text"]})
        _run_incident_in_background(r["event_text"], r["title"], r["region"])

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"status": "received"})


@app.route("/api/mail/status")
def mail_status():
    return jsonify({"configured": mailer.configured()})


@app.route("/api/incidents/<int:incident_id>/email", methods=["POST"])
def email_report(incident_id):
    """يبعت تقرير القرار (PDF متولّد بالواجهة) كمرفق لإيميل حقيقي."""
    data = request.get_json(force=True, silent=True) or {}
    incident = db.get_incident(incident_id)
    if incident is None:
        return jsonify({"error": "not found"}), 404
    try:
        pdf = base64.b64decode((data.get("pdf_base64") or "").split(",")[-1])
    except Exception:  # noqa: BLE001
        return jsonify({"error": "ملف PDF غير صالح"}), 400
    if len(pdf) < 500 or len(pdf) > 15 * 1024 * 1024:
        return jsonify({"error": "ملف PDF غير صالح"}), 400
    status_ar = "محسومة" if incident.get("status") == "resolved" else "غير محسومة"
    summary = data.get("summary") or ""
    subject = f"مدار — تقرير القضية #{incident_id}: {incident.get('title') or ''} ({status_ar})"
    text = f"تقرير القضية #{incident_id}\n{incident.get('title') or ''}\nالحالة: {status_ar}\n\n{summary}\n\nالتقرير الكامل مرفق كملف PDF."
    html = (f'<div dir="rtl" style="font-family:Tahoma,Arial;font-size:14px;color:#1b1f24">'
            f'<h2 style="color:#1f4e79;margin:0 0 6px">مدار — تقرير القضية #{incident_id}</h2>'
            f'<p style="margin:0 0 10px"><b>{incident.get("title") or ""}</b> — الحالة: <b>{status_ar}</b></p>'
            f'<p style="white-space:pre-wrap">{summary}</p>'
            f'<p style="color:#666">التقرير الكامل مرفق كملف PDF.</p></div>')
    try:
        sent = mailer.send_report(data.get("to") or [], subject, html, text, pdf,
                                  f"madar_case_{incident_id}.pdf")
    except mailer.MailError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "sent", "to": sent})


@app.route("/api/grid/network")
def grid_network():
    """الشبكة الأساسية (عقد + أضلاع) لرسمها على خريطة الأثر."""
    return jsonify(grid_graph.network_layer())


@app.route("/api/grid/impact/<incident_id>")
def grid_impact(incident_id):
    """
    Graph reasoning فعلي لحادثة: dijkstra + connected_components + betweenness_centrality
    على غراف networkx حقيقي. incident_id = رقم، أو "latest" لآخر حادثة انقفلت.
    """
    if incident_id == "latest":
        inc = next((i for i in db.list_incidents() if i.get("status") in ("resolved", "undecided")), None)
    else:
        try:
            inc = db.get_incident(int(incident_id))
        except ValueError:
            inc = None
    if not inc:
        return jsonify({"error": "ما في حادثة"}), 404
    try:
        return jsonify(grid_graph.analyze_incident(inc, db.list_trace_events(inc["id"])))
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"فشل تحليل الغراف: {e}"}), 500


@app.route("/api/approvals/<int:approval_id>/approve", methods=["POST"])
def approve_action(approval_id):
    data = request.get_json(force=True, silent=True) or {}
    result = approvals.approve(approval_id, decided_by=data.get("decided_by"), comment=data.get("comment"))
    event_bus.publish({"type": "approval_decided", "approval_id": approval_id, "status": "approved"})
    return jsonify(result)


@app.route("/api/approvals/<int:approval_id>/reject", methods=["POST"])
def reject_action(approval_id):
    data = request.get_json(force=True, silent=True) or {}
    result = approvals.reject(approval_id, decided_by=data.get("decided_by"), comment=data.get("comment"))
    event_bus.publish({"type": "approval_decided", "approval_id": approval_id, "status": "rejected"})
    return jsonify(result)


if __name__ == "__main__":
    # threaded=True مهم: لازم يخدم اتصال SSE طويل (/api/stream) وبنفس الوقت
    # يستقبل طلب /api/trigger بدون ما يعلّق
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)