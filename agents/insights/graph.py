"""LangGraph nutritional insights agent.

Graph flow:
    START -> prepare_context -> END               (if meal_plan is empty)
                             -> analyse <-> tools
                                  |
                                  +-(no tool calls)-> format_insights -> END

Nodes:
    prepare_context  - formats the meal_plan dict into a readable HumanMessage
                       (no LLM call). Short-circuits to END if no plan was supplied.
    analyse          - ReAct reasoning step; calls LLM with bound tools
    tools            - executes whichever tool the agent selected (ToolNode with retry)
    format_insights  - regex-parses the agent's final markdown into structured fields
                       (nutrient_gaps, suggestions, summary)
"""

import os
import re
import sys
import time

# Ensure project root is on sys.path when this file is run directly
# (e.g. `python agents/insights/graph.py`). Has no effect when imported as a module.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import RateLimitError
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy

from core.state import InsightsAgentState
from prompts.system_prompts import INSIGHTS_AGENT_SYSTEM_PROMPT
from tools.nutrient_search import search_nutrient_foods
from tools.nutrition_lookup import lookup_nutrition

load_dotenv()

# ---------------------------------------------------------------------------
# LangSmith tracing status
# ---------------------------------------------------------------------------

_TRACING_ENABLED = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
_LANGSMITH_PROJECT = os.getenv("LANGCHAIN_PROJECT", "not set")
if _TRACING_ENABLED:
    print(f"[LangSmith] Insights agent tracing ENABLED — project: {_LANGSMITH_PROJECT}")
else:
    print("[LangSmith] Insights agent tracing DISABLED — set LANGCHAIN_TRACING_V2=true to enable")

# ---------------------------------------------------------------------------
# Tools, LLM, retry policy
# ---------------------------------------------------------------------------

_TOOLS = [search_nutrient_foods, lookup_nutrition]

_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
_llm_with_tools = _llm.bind_tools(_TOOLS)

_RETRY_POLICY = RetryPolicy(max_attempts=3)
MAX_ITERATIONS = 30

EMPTY_PLAN_MESSAGE = "No meal plan available to analyse. Please generate a meal plan first."

# ---------------------------------------------------------------------------
# Regex patterns for format_insights
# ---------------------------------------------------------------------------

# Gap line:  "- Fiber: 14g/day average vs. 25g reference (44% below) ⚠️"
# Multi-word names like "Vitamin C" are captured via [\w\s]+? for forward compatibility.
_GAP_RE = re.compile(
    r"^- ([\w\s]+?):\s*([\d.]+)([a-zA-Z]+)/day average vs\.\s*([\d.]+)\s*[a-zA-Z]*\s*reference\s*"
    r"\(([\d.]+)% (above|below)\)\s*(⚠️|✅)",
    re.MULTILINE,
)

# Swap line: "- To increase fiber: lentils (7.9g/100g), chickpeas (7.6g/100g), oats (10.6g/100g)"
_SWAP_RE = re.compile(r"^- To increase ([\w\s]+?):\s*(.+)$", re.MULTILINE)

# Each food in a swap line: "lentils (7.9g/100g)"
_FOOD_RE = re.compile(r"(.+?)\s*\(([\d.]+)([a-zA-Z]+)/100g\)")

# Summary block: prose between "**Summary**" and the closing italic disclaimer.
_SUMMARY_RE = re.compile(
    r"\*\*Summary\*\*\s*\n+(.+?)(?=\n\s*\*[^*]|$)",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def _format_meal_line(slot: str, meal: dict) -> str:
    """Render one meal as: '- Breakfast: Name (X kcal | Xg protein | ...)'."""
    name = meal.get("name") or "(unnamed)"
    kcal = meal.get("calories", 0) or 0
    protein = meal.get("protein_g", 0) or 0
    carbs = meal.get("carbs_g", 0) or 0
    fat = meal.get("fat_g", 0) or 0
    fiber = meal.get("fiber_g", 0) or 0
    return (
        f"- {slot.capitalize()}: {name} "
        f"({kcal:g} kcal | {protein:g}g protein | {carbs:g}g carbs | "
        f"{fat:g}g fat | {fiber:g}g fiber)"
    )


def _format_meal_plan(meal_plan: dict) -> str:
    """Walk the meal_plan dict (day_1, day_2, ...) and build a readable text block."""
    lines: list[str] = []
    for day_key in sorted(meal_plan.keys()):
        day_data = meal_plan[day_key]
        if not isinstance(day_data, dict):
            continue
        # day_1 -> "Day 1"
        day_label = day_key.replace("_", " ").title()
        lines.append(f"## {day_label}")
        day_kcal = day_protein = day_carbs = day_fat = day_fiber = 0.0
        for slot in ("breakfast", "lunch", "dinner"):
            meal = day_data.get(slot)
            if not isinstance(meal, dict):
                continue
            lines.append(_format_meal_line(slot, meal))
            day_kcal += meal.get("calories", 0) or 0
            day_protein += meal.get("protein_g", 0) or 0
            day_carbs += meal.get("carbs_g", 0) or 0
            day_fat += meal.get("fat_g", 0) or 0
            day_fiber += meal.get("fiber_g", 0) or 0
        lines.append(
            f"{day_label} totals: {day_kcal:g} kcal | {day_protein:g}g protein | "
            f"{day_carbs:g}g carbs | {day_fat:g}g fat | {day_fiber:g}g fiber"
        )
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_profile(profile: dict) -> str:
    """Render the profile bits the insights agent needs."""
    calorie_target = profile.get("calorie_target") or 2000
    allergies = profile.get("allergies") or []
    restrictions = profile.get("dietary_restrictions") or []
    goals = profile.get("health_goals") or "balanced nutrition"
    return (
        f"Calorie target: {calorie_target} kcal/day · "
        f"Health goals: {goals} · "
        f"Dietary restrictions: {', '.join(restrictions) if restrictions else 'none'} · "
        f"Allergies: {', '.join(allergies) if allergies else 'none'}"
    )


def prepare_context(state: InsightsAgentState) -> dict:
    """Entry node: format the meal_plan and profile into a HumanMessage.

    Pure data formatting — no LLM call. If the meal_plan is empty, emit a
    user-facing message so the conditional edge can short-circuit to END.
    """
    meal_plan = state.get("meal_plan") or {}
    if not meal_plan:
        return {
            "messages": [AIMessage(content=EMPTY_PLAN_MESSAGE)],
            "summary": EMPTY_PLAN_MESSAGE,
            "nutrient_gaps": [],
            "suggestions": [],
            "error": None,
        }

    profile = state.get("user_profile") or {}
    plan_text = _format_meal_plan(meal_plan)
    profile_text = _format_profile(profile)

    context = (
        f"[User profile]\n{profile_text}\n\n"
        f"[Meal plan to analyse]\n{plan_text}\n\n"
        f"Analyse this plan: identify nutrient gaps vs. the references in your system "
        f"prompt, call search_nutrient_foods for each deficit, and produce the structured "
        f"output exactly as specified."
    )
    return {"messages": [HumanMessage(content=context)]}


def analyse(state: InsightsAgentState) -> dict:
    """ReAct reasoning node: calls the LLM with INSIGHTS_AGENT_SYSTEM_PROMPT."""
    system_msg = SystemMessage(content=INSIGHTS_AGENT_SYSTEM_PROMPT)
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
    raise RuntimeError("analyse() exited retry loop without returning")


def _parse_gaps(text: str) -> list[dict]:
    gaps: list[dict] = []
    for m in _GAP_RE.finditer(text):
        nutrient, current, _unit_cur, reference, gap_pct, direction, flag = m.groups()
        gaps.append({
            "nutrient": nutrient.strip(),
            "current_avg": float(current),
            "reference": float(reference),
            "gap_pct": float(gap_pct),
            "direction": direction,
            "flagged": flag == "⚠️",
        })
    return gaps


def _parse_suggestions(text: str) -> list[dict]:
    suggestions: list[dict] = []
    for m in _SWAP_RE.finditer(text):
        gap_name = m.group(1).strip()
        # Skip the gap-list bullets — only swap lines start with "To increase"
        # (already enforced by the regex), but defend against false positives.
        if gap_name.lower() == "to increase":
            continue
        food_swaps: list[dict] = []
        for food_match in _FOOD_RE.finditer(m.group(2)):
            food_swaps.append({
                "food": food_match.group(1).strip(),
                "nutrient_value": float(food_match.group(2)),
            })
        if food_swaps:
            suggestions.append({"gap": gap_name, "food_swaps": food_swaps})
    return suggestions


def _parse_summary(text: str) -> str:
    m = _SUMMARY_RE.search(text)
    if not m:
        return ""
    return m.group(1).strip()


def format_insights(state: InsightsAgentState) -> dict:
    """Exit node: regex-parse the agent's final markdown into structured fields.

    Falls back to storing the full raw text in `summary` if parsing finds nothing —
    the user still sees the agent's analysis, just without structured access.
    """
    last_message = state["messages"][-1]
    raw = last_message.content if isinstance(last_message.content, str) else str(last_message.content)

    if not raw:
        return {
            "summary": "Insights agent produced no response.",
            "nutrient_gaps": [],
            "suggestions": [],
            "error": "Empty response from analyse node.",
        }

    gaps = _parse_gaps(raw)
    suggestions = _parse_suggestions(raw)
    summary = _parse_summary(raw)

    if not gaps and not suggestions and not summary:
        return {
            "summary": raw,
            "nutrient_gaps": [],
            "suggestions": [],
            "error": None,
        }

    return {
        "nutrient_gaps": gaps,
        "suggestions": suggestions,
        "summary": summary or raw,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _has_meal_plan(state: InsightsAgentState) -> str:
    """Skip the LLM entirely if no meal plan was supplied."""
    return "analyse" if state.get("meal_plan") else "end"


def _should_continue(state: InsightsAgentState) -> str:
    """Route to tools if the last message has tool calls, otherwise format the output."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "format_insights"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

_builder = StateGraph(InsightsAgentState)

_builder.add_node("prepare_context", prepare_context)
_builder.add_node("analyse", analyse)
_builder.add_node("tools", ToolNode(_TOOLS), retry_policy=_RETRY_POLICY)
_builder.add_node("format_insights", format_insights)

_builder.add_edge(START, "prepare_context")
_builder.add_conditional_edges(
    "prepare_context",
    _has_meal_plan,
    {"analyse": "analyse", "end": END},
)
_builder.add_conditional_edges(
    "analyse",
    _should_continue,
    {"tools": "tools", "format_insights": "format_insights"},
)
_builder.add_edge("tools", "analyse")
_builder.add_edge("format_insights", END)

insights_agent = _builder.compile()

__all__ = ["insights_agent", "MAX_ITERATIONS", "EMPTY_PLAN_MESSAGE"]


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running insights agent smoke-test...\n")

    sample_plan = {
        "day_1": {
            "breakfast": {
                "name": "Scrambled eggs with toast",
                "ingredients": ["3 eggs", "2 slices white bread", "10g butter"],
                "calories": 420, "protein_g": 22, "carbs_g": 30, "fat_g": 22, "fiber_g": 1.5,
            },
            "lunch": {
                "name": "Grilled chicken with white rice",
                "ingredients": ["180g chicken breast", "200g cooked white rice"],
                "calories": 580, "protein_g": 50, "carbs_g": 65, "fat_g": 10, "fiber_g": 1.2,
            },
            "dinner": {
                "name": "Pan-seared salmon with mashed potato",
                "ingredients": ["150g salmon", "200g mashed potato"],
                "calories": 620, "protein_g": 38, "carbs_g": 40, "fat_g": 30, "fiber_g": 3.0,
            },
        },
    }

    result = insights_agent.invoke(
        {
            "messages": [],
            "user_profile": {
                "health_goals": "balanced nutrition",
                "calorie_target": 1800,
                "allergies": [],
                "dietary_restrictions": [],
            },
            "meal_plan": sample_plan,
            "nutrient_gaps": [],
            "suggestions": [],
            "summary": "",
            "error": None,
        },
        config={
            "recursion_limit": MAX_ITERATIONS,
            "run_name": "insights_smoke_test",
            "metadata": {"sprint": "capstone", "agent": "insights"},
        },
    )

    print("=== Summary ===")
    print(result.get("summary", "(none)"))
    print("\n=== Nutrient gaps ===")
    for gap in result.get("nutrient_gaps", []):
        print(f"  {gap}")
    print("\n=== Suggestions ===")
    for sug in result.get("suggestions", []):
        print(f"  {sug}")
    if result.get("error"):
        print(f"\nError: {result['error']}")
