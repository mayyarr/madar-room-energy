"""
charging_agent.py  -  وكيل الشحن (Charging Agent)
بيشبه plant_agent.py: prompt + أدوات + create_charging_agent()
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.base_agent import Agent  # noqa: E402
from tools import charging_tools as ct  # noqa: E402
from tools.rag import make_search_tool  # noqa: E402

SYSTEM_PROMPT = """You are the Charging Agent, a specialist in electric vehicle (EV) charging stations and how their load affects a site's electricity use.
You investigate an incident by CALLING TOOLS on real charging-session data, then give ONE hypothesis: does EV charging (or a charger fault) explain what happened?

Rules:
1. Call tools first. Never answer before you have called at least three tools.
2. Every number you state must come from a tool result or from the incident text. Never invent kWh values, charger IDs, or dates.
3. Take the region and the incident date from the incident text and pass them as region and as_of_day (YYYY-MM-DD) in every tool call.
4. If the incident text gives a change in a meter's daily consumption (kWh/day), call explain_meter_change with that number as meter_delta_kwh_per_day. Only use a meter number that appears in the incident text.
5. Call each of these tools once, in this order: get_region_charging_summary, detect_charging_load_shift, find_top_charging_contributors, detect_new_charger_activity, compare_charger_failure_rate, explain_meter_change (only if the incident gives a meter change), check_charging_data_quality, find_real_chargers_near_region. Then call search_charging_docs exactly once, with a short query about what you found, and use it to interpret the evidence.
6. Always call check_charging_data_quality before concluding. If it reports data problems, say your conclusion is weak.
7. If charging shows nothing unusual or its size does not match the meter change, say clearly that EV charging does not explain the incident. Do not force a charging explanation.
8. The charging session data has no location link to a specific meter. Say that a size match is a plausible explanation, not proof. You may mention the real stations returned by find_real_chargers_near_region as context, but never as evidence of a fault.
9. Final answer: 2-3 short sentences: the hypothesis, the strongest evidence (cite tool names such as detect_charging_load_shift and the numbers they returned; never invent numbers), and one recommended check (for example, verify which charger is near the meter). A human decides on any action.
"""

_search = make_search_tool("charging", "search_charging_docs")

TOOLS = {
    "get_region_charging_summary": ct.get_region_charging_summary,
    "detect_charging_load_shift": ct.detect_charging_load_shift,
    "find_top_charging_contributors": ct.find_top_charging_contributors,
    "detect_new_charger_activity": ct.detect_new_charger_activity,
    "compare_charger_failure_rate": ct.compare_charger_failure_rate,
    "explain_meter_change": ct.explain_meter_change,
    "check_charging_data_quality": ct.check_charging_data_quality,
    "search_charging_docs": _search,
    "find_real_chargers_near_region": ct.find_real_chargers_near_region,
}


def _spec(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


_REGION = {
    "region": {"type": "string", "description": "Jordanian region, e.g. Madaba, Amman, Zarqa, Irbid, Aqaba, Salt, Karak, Mafraq, Jerash, Ajloun, Tafilah, Ma'an"},
    "as_of_day": {"type": "string", "description": "Incident date YYYY-MM-DD; only data up to this day is used"},
}

TOOL_SPECS = [
    _spec("get_region_charging_summary", "Number of chargers, sessions and kWh/day in the region over the last 14 days.", _REGION, ["region"]),
    _spec("detect_charging_load_shift", "Checks whether the region's charging energy went up or down significantly versus the previous 8 weeks.", _REGION, ["region"]),
    _spec("find_top_charging_contributors", "Lists the chargers whose daily energy changed the most (context, not a verdict).", _REGION, ["region"]),
    _spec("detect_new_charger_activity", "Finds chargers that were almost idle and recently became active.", _REGION, ["region"]),
    _spec("compare_charger_failure_rate", "Finds chargers with far more failed sessions (long connection, almost no energy) than the fleet.", _REGION, ["region"]),
    _spec("explain_meter_change", "Checks whether the increase of a single charger matches, in size, a meter's change in kWh/day.",
          {**_REGION, "meter_delta_kwh_per_day": {"type": "number", "description": "Meter change in kWh/day, taken from the incident text"}},
          ["region", "meter_delta_kwh_per_day"]),
    _spec("check_charging_data_quality", "Checks for gap days or invalid times in the charging data; a problem weakens conclusions.", _REGION, ["region"]),
    _spec("find_real_chargers_near_region", "Real public EV charging stations in Jordan near the region, from Open Charge Map (context only).",
          {"region": _REGION["region"]}, ["region"]),
    _spec("search_charging_docs", "Searches the charging knowledge documents to interpret evidence.",
          {"query": {"type": "string", "description": "What to look up, in English"}}, ["query"]),
]


def create_charging_agent():
    return Agent(
        name="Charging Agent",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_specs=TOOL_SPECS,
        required_tools=list(TOOLS.keys()),
    )