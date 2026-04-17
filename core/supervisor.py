"""LangGraph supervisor — routes user messages to sub-agents.

Graph flow:
    START -> route -> run_meal_planner -> END
                   -> run_insights     -> END
                   -> clarify          -> END

The `route` node performs LLM-based intent classification (gpt-4.1-mini with
SUPERVISOR_ROUTING_PROMPT) and sets `state["route_to"]`. A conditional edge
dispatches to one of the three terminal nodes.

The meal planner sub-agent (`meal_agent` from core.graph) still uses its own
AgentState internally — `run_meal_planner` maps SupervisorState fields into
that shape, invokes the inner graph, and maps the results back.
"""

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END

from core.state import SupervisorState
from core.graph import meal_agent, MAX_ITERATIONS
from prompts.system_prompts import SUPERVISOR_ROUTING_PROMPT


_VALID_ROUTES = {"meal_planner", "insights", "clarify"}

_router_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _last_user_text(messages: list) -> str:
    """Return the most recent HumanMessage content, or "" if none exists."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def route(state: SupervisorState) -> dict:
    """LLM-based intent classification — sets state['route_to']."""
    user_text = _last_user_text(state["messages"])
    if not user_text:
        return {"route_to": "clarify"}

    response = _router_llm.invoke([
        SystemMessage(content=SUPERVISOR_ROUTING_PROMPT),
        HumanMessage(content=user_text),
    ])
    content = response.content
    raw = content if isinstance(content, str) else str(content)
    raw = raw.strip()

    # Strip ``` fences if the LLM wraps the JSON (same defence as
    # format_output in core/graph.py).
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
        route_val = parsed.get("route", "clarify")
    except (json.JSONDecodeError, ValueError, TypeError):
        route_val = "clarify"

    if route_val not in _VALID_ROUTES:
        route_val = "clarify"
    return {"route_to": route_val}


def run_meal_planner(state: SupervisorState) -> dict:
    """Invoke the Sprint 3 meal planner with a mapped-in AgentState."""
    inner_state = {
        "messages": state["messages"],
        "user_id": state.get("user_id", "default_user"),
        "user_profile": state.get("user_profile") or {},
        "meal_plan": {},
        "shopping_list": [],
        "current_step": "start",
        "error": None,
    }
    result = meal_agent.invoke(
        inner_state,
        config={
            "recursion_limit": MAX_ITERATIONS,
            "run_name": "meal_plan_generation",
            "metadata": {
                "user_id": state.get("user_id", "default_user"),
                "sprint": "capstone",
                "invoked_by": "supervisor",
            },
        },
    )
    inner_messages = result.get("messages") or []
    last_msg = inner_messages[-1] if inner_messages else AIMessage(content="")
    return {
        "messages": [last_msg],
        "meal_plan": result.get("meal_plan", {}),
        "shopping_list": result.get("shopping_list", []),
        "error": result.get("error"),
    }


def run_insights(state: SupervisorState) -> dict:
    """Placeholder — the Nutritional Insights agent graph is not built yet."""
    return {
        "messages": [AIMessage(
            content="Nutritional Insights agent coming soon — this feature is under development."
        )],
    }


def clarify(state: SupervisorState) -> dict:
    """Fallback when the router can't confidently classify the request."""
    return {
        "messages": [AIMessage(
            content="I can help with meal planning or nutritional analysis. What would you like to do?"
        )],
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _route_decider(state: SupervisorState) -> str:
    return state["route_to"]


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

_builder = StateGraph(SupervisorState)

_builder.add_node("route", route)
_builder.add_node("run_meal_planner", run_meal_planner)
_builder.add_node("run_insights", run_insights)
_builder.add_node("clarify", clarify)

_builder.add_edge(START, "route")
_builder.add_conditional_edges(
    "route",
    _route_decider,
    {
        "meal_planner": "run_meal_planner",
        "insights":     "run_insights",
        "clarify":      "clarify",
    },
)
_builder.add_edge("run_meal_planner", END)
_builder.add_edge("run_insights", END)
_builder.add_edge("clarify", END)

supervisor_agent = _builder.compile()

__all__ = ["supervisor_agent", "MAX_ITERATIONS"]
