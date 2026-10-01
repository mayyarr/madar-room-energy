"""
chaos.py - وضع الفوضى (Chaos Mode) لأغراض العرض الحي فقط (P9.3)
تعليمة يدوية بسيطة: "اجعلي الوكيل الفلاني يفشل بالتشغيلة الجاية"، عشان نثبت للجنة
إنه فشل وكيل واحد ما بيكسر التحقيق كامل (dispatch/_run_one بيلتقطوه ويكملوا بالباقي).
one-shot: بمجرد ما الوكيل المستهدف يوصل لأول استدعاء أداة، الفوضى تنفّذ وتنطفي لحالها.
"""
import random
import threading

_lock = threading.Lock()
_armed_agent = {"name": None}

AGENTS = ["Meter Agent", "Plant Agent", "Charging Agent"]


def arm(agent_name=None):
    """يسلّح وضع الفوضى لوكيل محدد، أو لوكيل عشوائي إذا ما تحدد."""
    name = agent_name if agent_name in AGENTS else random.choice(AGENTS)
    with _lock:
        _armed_agent["name"] = name
    return name


def disarm():
    with _lock:
        _armed_agent["name"] = None


def should_fail(agent_name):
    """يُستدعى من base_agent قبل أول استدعاء أداة. one-shot: بينطفي فور الاستخدام."""
    with _lock:
        if _armed_agent["name"] == agent_name:
            _armed_agent["name"] = None
            return True
        return False
