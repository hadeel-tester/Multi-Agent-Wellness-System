"""LangGraph supervisor — routes user messages to sub-agents.

Graph flow:
    START -> route -> run_meal_planner -> END
                   -> run_insights     -> END
                   -> run_check_in     -> END
                   -> clarify          -> END

The `route` node performs LLM-based intent classification (gpt-4.1-mini with
SUPERVISOR_ROUTING_PROMPT) and sets `state["route_to"]`. A conditional edge
dispatches to one of the four terminal nodes.

The meal planner sub-agent (`meal_agent` from core.graph) still uses its own
AgentState internally — `run_meal_planner` maps SupervisorState fields into
that shape, invokes the inner graph, and maps the results back. The insights
and check-in sub-agents follow the same pattern.
"""

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END

from core.state import SupervisorState
from core.graph import meal_agent, MAX_ITERATIONS
from prompts.system_prompts import SUPERVISOR_ROUTING_PROMPT


_VALID_ROUTES = {"meal_planner", "insights", "check_in", "clarify"}

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
        "messages": list(state["messages"]),
        "user_id": state.get("user_id", "default_user"),
        "user_profile": state.get("user_profile") or {},
        "meal_plan": {},
        "shopping_list": [],
        "current_step": "start",
        "error": None,
    }
    check_in_history = state.get("check_in_history") or []
    if not check_in_history:
        # Fallback: pull the latest persisted check-in so feedback survives
        # across browser sessions even when session state is empty.
        from core.memory import load_recent_check_ins

        recent = load_recent_check_ins(state.get("user_id", "default_user"), limit=1)
        if recent:
            latest_notes = recent[0].get("notes") or ""
            if latest_notes:
                check_in_history = [latest_notes]
    if check_in_history:
        latest = check_in_history[0]
        check_in_context = (
            f"[Previous check-in feedback — apply as soft preferences]\n"
            f"{latest}\n\n"
            f"Rules for applying feedback:\n"
            f"- 'Found X boring/repetitive' means include X at least once but no more than once "
            f"across the full plan. Do NOT drop X to zero — reducing is not eliminating.\n"
            f"- 'Would like Y added' means include Y roughly at the frequency the user suggested. "
            f"For example, 'once every three days' in a 4-day plan means 1 occurrence.\n"
            f"- These preferences do not override dietary restrictions, allergen rules, or calorie targets."
        )
        inner_state["user_profile"] = {
            **inner_state["user_profile"],
            "check_in_context": check_in_context,
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
    """Invoke the Nutritional Insights agent."""
    from agents.insights.graph import insights_agent

    meal_plan = state.get("meal_plan") or {}
    if not meal_plan:
        return {
            "messages": [AIMessage(
                content="No meal plan available to analyse. Please generate a meal plan first, then ask for nutritional insights."
            )],
        }

    inner_state = {
        "messages": state["messages"],
        "user_profile": state.get("user_profile") or {},
        "meal_plan": meal_plan,
        "nutrient_gaps": [],
        "suggestions": [],
        "summary": "",
        "error": None,
    }
    result = insights_agent.invoke(
        inner_state,
        config={
            "recursion_limit": 40,
            "run_name": "nutritional_insights",
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
        "insights": {
            "nutrient_gaps": result.get("nutrient_gaps", []),
            "suggestions": result.get("suggestions", []),
            "summary": result.get("summary", ""),
        },
        "error": result.get("error"),
    }


def run_check_in(state: SupervisorState) -> dict:
    """Invoke the Check-In agent."""
    from agents.checkin.graph import checkin_agent

    inner_state = {
        "messages": state["messages"],
        "user_profile": state.get("user_profile") or {},
        "user_id": state.get("user_id", "default_user"),
        "check_in_data": {},
        "summary": "",
        "error": None,
    }
    result = checkin_agent.invoke(
        inner_state,
        config={
            "recursion_limit": 10,
            "run_name": "weekly_check_in",
            "metadata": {
                "user_id": state.get("user_id", "default_user"),
                "sprint": "capstone",
                "invoked_by": "supervisor",
            },
        },
    )
    inner_messages = result.get("messages") or []
    last_msg = inner_messages[-1] if inner_messages else AIMessage(content="")

    # Update check_in_history so the meal planner can reference it later.
    existing_history = list(state.get("check_in_history") or [])
    new_summary = result.get("summary") or ""
    if new_summary:
        existing_history = [new_summary] + existing_history[:1]  # keep latest 2

    return {
        "messages": [last_msg],
        "check_in_history": existing_history,
        "error": result.get("error"),
    }


def clarify(state: SupervisorState) -> dict:
    """Fallback when the router can't confidently classify the request."""
    return {
        "messages": [AIMessage(
            content="I can help with meal planning, nutritional insights, or a weekly check-in. What would you like to do?"
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
_builder.add_node("run_check_in", run_check_in)
_builder.add_node("clarify", clarify)

_builder.add_edge(START, "route")
_builder.add_conditional_edges(
    "route",
    _route_decider,
    {
        "meal_planner": "run_meal_planner",
        "insights":     "run_insights",
        "check_in":     "run_check_in",
        "clarify":      "clarify",
    },
)
_builder.add_edge("run_meal_planner", END)
_builder.add_edge("run_insights", END)
_builder.add_edge("run_check_in", END)
_builder.add_edge("clarify", END)

supervisor_agent = _builder.compile()

__all__ = ["supervisor_agent", "MAX_ITERATIONS"]
