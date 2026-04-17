"""Agent state — single source of truth for the LangGraph meal planning agent.

Only `messages` uses a reducer (add_messages) because it accumulates across
all nodes. All other fields are set/overwritten by specific nodes.

AgentState      — internal state for the Sprint 3 meal planner (core/graph.py).
SupervisorState — outer wrapper used by the multi-agent supervisor (core/supervisor.py).
                  The meal planner still receives/returns AgentState internally.
InsightsAgentState — internal state for the nutritional insights agent (agents/insights/graph.py).
"""

from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    user_profile: dict          # health_goals, dietary_restrictions, calorie_target, allergies, sex
    meal_plan: dict             # keyed monday–sunday, each with breakfast/lunch/dinner
    shopping_list: list[str]
    current_step: str
    user_id: str
    error: str | None
    calorie_retries: int


class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    user_profile: dict          # shared profile passed to every sub-agent
    user_id: str
    route_to: str               # "meal_planner" | "insights" | "check_in" | "clarify"
    meal_plan: dict             # last meal plan produced by the meal planner agent
    shopping_list: list[str]    # last shopping list produced by the meal planner agent
    insights: dict              # last insights produced by the insights agent
    check_in_history: list      # ordered list of check-in summaries (latest last)
    error: str | None
    calorie_retries: int


class InsightsAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    user_profile: dict
    meal_plan: dict              # input from supervisor (meal planner output)
    nutrient_gaps: list          # [{nutrient, current_avg, reference, gap_pct}]
    suggestions: list            # [{gap, food_swaps: [{food, amount, nutrient_value}]}]
    summary: str                 # overall narrative
    error: str | None
