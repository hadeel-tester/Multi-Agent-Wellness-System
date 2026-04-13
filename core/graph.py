"""LangGraph meal planning agent.

Graph flow:
    START -> load_profile -> agent <-> tools -> format_output -> END

Nodes:
    load_profile   - reads user_id from state, injects profile before the ReAct loop
    agent          - ReAct reasoning step; calls LLM with bound tools
    tools          - executes whichever tool the agent selected (ToolNode with retry)
    format_output  - extracts structured meal_plan and shopping_list from agent response
"""

import json
import os
import sys
import time

# Ensure project root is on sys.path when this file is run directly
# (e.g. `python core/graph.py`). Has no effect when imported as a module.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import RateLimitError
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy

from core.state import AgentState
from core.memory import init_db, load_profile as db_load_profile
from prompts.system_prompts import MEAL_PLANNER_SYSTEM_PROMPT, FORMAT_OUTPUT_PROMPT
from tools.nutrition_lookup import lookup_nutrition
from tools.allergen_checker import check_allergens
from tools.health_scorer import score_meal_health
from tools.rag_validator import validate_meal_safety

load_dotenv()
init_db()

# ---------------------------------------------------------------------------
# LangSmith tracing status
# ---------------------------------------------------------------------------

_TRACING_ENABLED = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
_LANGSMITH_PROJECT = os.getenv("LANGCHAIN_PROJECT", "not set")
if _TRACING_ENABLED:
    print(f"[LangSmith] Tracing ENABLED — project: {_LANGSMITH_PROJECT}")
else:
    print("[LangSmith] Tracing DISABLED — set LANGCHAIN_TRACING_V2=true to enable")

# CAPSTONE: Add supervisor agent to orchestrate meal planner + supplement + check-in agents

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

_TOOLS = [lookup_nutrition, check_allergens, score_meal_health, validate_meal_safety]

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
_llm_with_tools = _llm.bind_tools(_TOOLS)

# ---------------------------------------------------------------------------
# Retry policy & iteration limit
# ---------------------------------------------------------------------------

_RETRY_POLICY = RetryPolicy(max_attempts=3)  # initial attempt + 2 retries
MAX_ITERATIONS = 80  # headroom for 4 tools x multiple meals; prevents infinite loops

# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

DEFAULT_PROFILE = {
    "health_goals": "balanced nutrition",
    "dietary_restrictions": [],
    "calorie_target": 2000,
    "allergies": [],
}


def load_profile(state: AgentState) -> dict:
    """Entry node: load user profile from SQLite and inject into state.

    If a profile is found, a HumanMessage summarising it is prepended to
    state["messages"] so the agent has explicit profile context from turn 1.
    If no profile is found, a sensible default is used.
    """
    user_id = state.get("user_id", "default")
    profile = db_load_profile(user_id)
    profile_found = profile is not None
    if not profile_found:
        profile = DEFAULT_PROFILE.copy()

    updates: dict = {"user_profile": profile, "current_step": "agent"}

    if profile_found:
        restrictions = profile.get("dietary_restrictions") or []
        allergies = profile.get("allergies") or []
        summary = (
            f"[User profile loaded]\n"
            f"Name: {profile.get('name') or 'not set'}\n"
            f"Age: {profile.get('age') or 'not set'}\n"
            f"Weight: {profile.get('weight_kg') or 'not set'} kg  "
            f"Height: {profile.get('height_cm') or 'not set'} cm\n"
            f"Health goals: {profile.get('health_goals') or 'balanced nutrition'}\n"
            f"Dietary restrictions: {', '.join(restrictions) if restrictions else 'none'}\n"
            f"Allergies: {', '.join(allergies) if allergies else 'none'}\n"
            f"Calorie target: {profile.get('calorie_target') or 2000} kcal/day"
        )
        updates["messages"] = [HumanMessage(content=summary)]

    return updates


def agent(state: AgentState) -> dict:
    """ReAct reasoning node: calls the LLM with the current message history.

    Prepends a system message that includes the user's health profile so the
    LLM can personalise its recommendations on every turn.
    """
    profile = state.get("user_profile") or DEFAULT_PROFILE
    profile_text = (
        f"Health goals: {profile.get('health_goals', 'balanced nutrition')}\n"
        f"Dietary restrictions: {', '.join(profile.get('dietary_restrictions', [])) or 'none'}\n"
        f"Calorie target: {profile.get('calorie_target', 2000)} kcal/day\n"
        f"Allergies: {', '.join(profile.get('allergies', [])) or 'none'}"
    )
    system_msg = SystemMessage(
        content=f"{MEAL_PLANNER_SYSTEM_PROMPT}\n\n## User health profile\n{profile_text}"
    )
    messages = [system_msg] + list(state["messages"])
    for attempt in range(3):
        try:
            response = _llm_with_tools.invoke(messages)
            return {"messages": [response]}
        except RateLimitError:
            if attempt < 2:
                time.sleep(2)
                continue
            raise
    # Unreachable: loop always returns or raises, but satisfies type checker
    raise RuntimeError("agent() exited retry loop without returning")


def format_output(state: AgentState) -> dict:
    """Post-processing node: extract structured meal_plan and shopping_list.

    Runs once after the ReAct loop ends (no more tool calls). Uses a dedicated
    FORMAT_OUTPUT_PROMPT to instruct the LLM to parse the agent's final message
    into JSON, then writes the result into state.
    """
    last_message = state["messages"][-1]
    agent_response = last_message.content

    if not agent_response:
        return {"error": "Agent produced no final response to format."}

    prompt = FORMAT_OUTPUT_PROMPT.format(agent_response=agent_response)

    try:
        response = _llm.invoke(prompt)
        content = response.content
        # ChatOpenAI normally returns str, but BaseMessage.content can be list[...]
        raw = content if isinstance(content, str) else str(content)
        raw = raw.strip()
        # Strip markdown fences if the LLM wraps the JSON
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        parsed = json.loads(raw)
        return {
            "meal_plan": parsed.get("meal_plan", {}),
            "shopping_list": parsed.get("shopping_list", []),
            "current_step": "done",
        }
    except (json.JSONDecodeError, Exception) as exc:
        return {
            "error": f"Failed to parse structured output: {exc}",
            "current_step": "done",
        }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def should_continue(state: AgentState) -> str:
    """Route to tools if the last message has tool calls, otherwise format output."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "format_output"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

_builder = StateGraph(AgentState)

_builder.add_node("load_profile", load_profile)
_builder.add_node("agent", agent)
_builder.add_node("tools", ToolNode(_TOOLS), retry_policy=_RETRY_POLICY)
_builder.add_node("format_output", format_output)

_builder.add_edge(START, "load_profile")
_builder.add_edge("load_profile", "agent")
_builder.add_conditional_edges(
    "agent",
    should_continue,
    {"tools": "tools", "format_output": "format_output"},
)
_builder.add_edge("tools", "agent")
_builder.add_edge("format_output", END)

meal_agent = _builder.compile()

# ---------------------------------------------------------------------------
# Quick smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running smoke-test invocation...\n")
    _smoke_user_id = "test_user_001"
    result = meal_agent.invoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "I'm 30 years old with a weight loss goal. "
                        "My daily calorie target is 1800 kcal and I have a gluten allergy. "
                        "Please plan 1 day of meals (breakfast, lunch, dinner)."
                    )
                )
            ],
            "user_id": _smoke_user_id,
            "user_profile": {
                "age": 30,
                "health_goals": "weight loss",
                "calorie_target": 1800,
                "allergies": ["gluten"],
                "dietary_restrictions": [],
            },
            "meal_plan": {},
            "shopping_list": [],
            "current_step": "start",
            "error": None,
        },
        config={
            "recursion_limit": MAX_ITERATIONS,
            "run_name": "meal_plan_generation",
            "metadata": {"user_id": _smoke_user_id, "sprint": "sprint3"},
        },
    )

    print("=== Messages ===")
    for msg in result["messages"]:
        label = type(msg).__name__
        content = msg.content if msg.content else f"[tool_calls: {msg.tool_calls}]"
        text = str(content)
        print(f"{label}: {text[:200]}...\n" if len(text) > 200 else f"{label}: {text}\n")

    print("=== Structured Output ===")
    meal_plan = result.get("meal_plan", {})
    shopping = result.get("shopping_list", [])
    print(f"Meal plan keys: {list(meal_plan.keys())}")
    print(f"Shopping list ({len(shopping)} items): {shopping[:10]}...")
    if result.get("error"):
        print(f"Error: {result['error']}")
