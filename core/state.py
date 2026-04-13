"""Agent state — single source of truth for the LangGraph meal planning agent.

Only `messages` uses a reducer (add_messages) because it accumulates across
all nodes. All other fields are set/overwritten by specific nodes.
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
