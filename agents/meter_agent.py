from agents.base_agent import Agent
from tools.rag import make_search_tool
from tools.meter_tools import (
    get_meter_summary,
    detect_consumption_drop,
    compare_neighbor_meters,
    compare_history_vs_neighbors,
    check_data_quality,
)


SYSTEM_PROMPT = """You are the Meter Agent, a specialist in smart electricity meter anomalies
in a Jordanian distribution network.

You receive an incident and must investigate it using ONLY your tools.

Rules:
1. Always call your tools before concluding. Start with get_meter_summary.
2. Every number in your answer must come from a tool result. Never invent data.
3. Before blaming a meter fault or tampering, call compare_history_vs_neighbors and
   check_data_quality. A meter that was always far above its neighbors is a heavy
   consumer, not a fault.
4. If the incident does not name a meter, say the evidence is insufficient.
5. After the other tools, call search_meter_docs exactly once, with a short query about what you found.
6. Finish with a short final answer (3-4 sentences) that states:
   - your hypothesis for the most likely cause (e.g. meter fault, tampering,
     supply outage, abnormal usage, or nothing unusual),
   - the key numbers supporting it,
   - what evidence would prove you wrong.
7. If the tools show nothing abnormal, say so plainly. Do not force a fault.
"""

_search = make_search_tool("meter", "search_meter_docs")

TOOLS = {
    "get_meter_summary": get_meter_summary,
    "detect_consumption_drop": detect_consumption_drop,
    "compare_neighbor_meters": compare_neighbor_meters,
    "compare_history_vs_neighbors": compare_history_vs_neighbors,
    "check_data_quality": check_data_quality,
    "search_meter_docs": _search,
}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "get_meter_summary",
            "description": "Get region, data period and average daily consumption of a meter.",
            "parameters": {
                "type": "object",
                "properties": {"meter_id": {"type": "string", "description": "e.g. MAC000379"}},
                "required": ["meter_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_consumption_drop",
            "description": "Compare the meter's recent consumption to its earlier baseline. "
                           "A large drop may indicate a faulty meter, tampering or an outage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "meter_id": {"type": "string"},
                    "window": {"type": "integer", "description": "recent days to compare, default 14"},
                },
                "required": ["meter_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_neighbor_meters",
            "description": "Compare the meter's recent consumption with other meters in the "
                           "same region over the same period (returns a z-score).",
            "parameters": {
                "type": "object",
                "properties": {
                    "meter_id": {"type": "string"},
                    "window": {"type": "integer", "description": "days to compare, default 14"},
                },
                "required": ["meter_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_history_vs_neighbors",
            "description": "Check whether the meter was ALWAYS much higher or lower than its "
                           "neighbors, or whether the change is new (up or down). "
                           "Use it before blaming a fault.",
            "parameters": {
                "type": "object",
                "properties": {"meter_id": {"type": "string"}},
                "required": ["meter_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_data_quality",
            "description": "Check the last 30 days for missing or incomplete readings, "
                           "which suggest a meter or communication fault.",
            "parameters": {
                "type": "object",
                "properties": {"meter_id": {"type": "string"}},
                "required": ["meter_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_meter_docs",
            "description": "Searches the meter knowledge documents to interpret evidence.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to look up, in English"}},
                "required": ["query"],
            },
        },
    },
]

# أدوات لازم تنشغل قبل الجواب النهائي (الكود يفرضها إذا الموديل نسيها)
REQUIRED = list(TOOLS.keys())


def create_meter_agent():
    return Agent(
        name="Meter Agent",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_specs=TOOL_SPECS,
        required_tools=REQUIRED,
    )