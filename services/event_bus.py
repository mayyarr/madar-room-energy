"""
services/event_bus.py - ناقل الأحداث الحي (P6.2)

نمط Pub/Sub بسيط بالذاكرة: كل متصفح (SSE client) يشترك بطابور (Queue) خاص فيه،
و publish() تنشر أي حدث لكل المشتركين الحاليين. هيك أي حدث emit من الوكلاء أو
الـ Arbitrator (tool_call, tool_result, hypothesis, challenge, debate_result...)
بيوصل فوراً لكل المتصفحات المفتوحة بدون ما يحتاج polling.

ملاحظة: طابور بالذاكرة بس (مش Redis) — كافي لعرض حي واحد بجلسة هاكاثون، ومو مصمم
لعدة عمليات (processes) منفصلة. لو الخادم اشتغل بأكتر من worker (gunicorn -w 2+)
لازم ننتقل لناقل خارجي حقيقي.
"""
import queue
import threading
import time

_subscribers = set()
_lock = threading.Lock()


def subscribe():
    """يرجع طابور جديد خاص بمتصفح واحد. يُنادى مرة وحدة لكل اتصال SSE."""
    q = queue.Queue(maxsize=200)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q):
    """يشال الطابور لما المتصفح يسكر الاتصال (أو ينقطع)."""
    with _lock:
        _subscribers.discard(q)


def publish(event: dict):
    """
    ينشر حدث واحد لكل المشتركين الحاليين. هاد بالضبط الدالة يلي بننادها من
    emit=event_bus.publish عند تشغيل run_and_save، فكل event يطلع من الوكلاء
    أو الـ Arbitrator بيوصل مباشرة.
    ما بترمي استثناء أبداً حتى لو فشل مشترك واحد (طابور ممتلئ مثلاً) — البث
    الحي ثانوي، ما لازم يوقف التحقيق نفسه.
    """
    event = dict(event)
    event.setdefault("ts", time.time())
    with _lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


def subscriber_count():
    with _lock:
        return len(_subscribers)