"""
plant_agent.py  -  وكيل المعدات الصناعية (Plant Agent)
بيشبه meter_agent.py: prompt + أدوات + create_plant_agent()
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.base_agent import Agent  # noqa: E402
from tools import plant_tools as pt  # noqa: E402
from tools.rag import make_search_tool  # noqa: E402

SYSTEM_PROMPT = """You are the Plant Agent, a specialist in industrial equipment health (turbine/engine units at power plants).
You investigate an incident by CALLING TOOLS on the unit's real sensor data, then give ONE hypothesis.

Rules:
1. Call tools first. Never answer before you have called at least three tools.
2. Every number you state must come from a tool result. Never invent cycles, temperatures, or lifetimes.
3. If the incident text mentions a cycle (for example "as of cycle 187"), 
   pass it as as_of_cycle in every tool call. Always pass unit_id.
   If the incident text does not clearly name a plant unit ID (a small 
   integer for an engine, not a meter ID like "MACxxxxxx"), do not guess: 
   say plainly that this incident does not name a plant unit and give 
   confidence near zero without calling get_unit_summary.
4. Always call check_sensor_quality before blaming the engine. If it finds a sensor problem, your hypothesis is a sensor/instrument fault, not engine wear.
5. Useful order: get_unit_summary, detect_thermal_anomaly, get_degradation_trend, compare_engine_to_fleet, estimate_remaining_life, check_sensor_quality. Then call search_maintenance_docs exactly once, with a short query about the fault you found, and use it to explain the likely fault mechanism.
6. If nothing is abnormal, say plainly that nothing unusual was found. Do not exaggerate.
7. When you give the estimated remaining life, state the range too and say it is an estimate from similar engines, not a certainty.
8. Final answer: 2-3 short sentences. State the hypothesis, the strongest evidence, and the recommended check. Do not recommend shutdown by yourself; a human decides.
9. Do not name specific engine components (blades, combustor, etc.) unless a tool or document result names them. Wear rates are in "health index per cycle", not "cycles".
"""

_search = make_search_tool("plant", "search_maintenance_docs")

TOOLS = {
    "get_unit_summary": pt.get_unit_summary,
    "detect_thermal_anomaly": pt.detect_thermal_anomaly,
    "estimate_remaining_life": pt.estimate_remaining_life,
    "compare_engine_to_fleet": pt.compare_engine_to_fleet,
    "get_degradation_trend": pt.get_degradation_trend,
    "check_sensor_quality": pt.check_sensor_quality,
    "search_maintenance_docs": _search,
}


def _spec(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


_UNIT = {
    "unit_id": {"type": "integer", "description": "Engine/unit number, for example 1"},
    "as_of_cycle": {"type": "integer",
                    "description": "Treat this cycle as 'now' (only use data up to it). Take it from the incident text if given."},
}

TOOL_SPECS = [
    _spec("get_unit_summary", "Site, cycles observed and current health index of the unit.", _UNIT, ["unit_id"]),
    _spec("detect_thermal_anomaly", "Checks whether the temperature sensors (s2,s3,s4) are abnormally high versus the unit's own baseline.", _UNIT, ["unit_id"]),
    _spec("estimate_remaining_life", "Estimates remaining useful life (cycles) by comparing with other engines in a similar condition.", _UNIT, ["unit_id"]),
    _spec("compare_engine_to_fleet", "Compares this unit's wear with the fleet at the same age.", _UNIT, ["unit_id"]),
    _spec("get_degradation_trend", "Checks whether wear is rising and accelerating over the last 30 cycles.", _UNIT, ["unit_id"]),
    _spec("check_sensor_quality", "Checks for stuck, missing, or single-sensor jumps that would mean a sensor fault instead of engine wear.", _UNIT, ["unit_id"]),
    _spec("search_maintenance_docs", "Searches the maintenance/knowledge documents to interpret sensor signals.",
          {"query": {"type": "string", "description": "What to look up, in English"}}, ["query"]),
]


def create_plant_agent():
    return Agent(
        name="Plant Agent",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_specs=TOOL_SPECS,
        required_tools=list(TOOLS.keys()),
    )