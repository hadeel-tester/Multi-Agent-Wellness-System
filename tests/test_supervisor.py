"""Unit tests for supervisor routing logic (core/supervisor.py).

Tests the `route` node function directly — no full agent execution,
no real LLM calls, no API key required.

Run from project root:
    pytest tests/test_supervisor.py -v
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(user_text: str) -> dict:
    return {
        "messages": [HumanMessage(content=user_text)],
        "user_id": "test_user",
        "user_profile": {},
        "meal_plan": {},
        "shopping_list": [],
        "current_step": "start",
        "error": None,
        "route_to": "",
        "insights": {},
        "check_in_history": [],
    }


def _mock_llm_response(json_payload: str):
    """Return a mock that makes _router_llm.invoke() return json_payload."""
    mock_response = MagicMock()
    mock_response.content = json_payload
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = mock_response
    return mock_llm


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------

@patch("core.supervisor._router_llm", _mock_llm_response('{"route": "meal_planner"}'))
def test_routes_meal_planning_request():
    from core.supervisor import route
    result = route(_make_state("Plan 3 days of healthy meals"))
    assert result["route_to"] == "meal_planner"


@patch("core.supervisor._router_llm", _mock_llm_response('{"route": "insights"}'))
def test_routes_nutrient_gap_question():
    from core.supervisor import route
    result = route(_make_state("What nutrients am I missing?"))
    assert result["route_to"] == "insights"


@patch("core.supervisor._router_llm", _mock_llm_response('{"route": "insights"}'))
def test_routes_analyze_meal_plan():
    from core.supervisor import route
    result = route(_make_state("Analyze my meal plan"))
    assert result["route_to"] == "insights"


@patch("core.supervisor._router_llm", _mock_llm_response('{"route": "clarify"}'))
def test_routes_ambiguous_greeting():
    from core.supervisor import route
    result = route(_make_state("Hello"))
    assert result["route_to"] == "clarify"


# ---------------------------------------------------------------------------
# Fallback / defensive behaviour
# ---------------------------------------------------------------------------

@patch("core.supervisor._router_llm", _mock_llm_response("not valid json"))
def test_falls_back_to_clarify_on_bad_json():
    from core.supervisor import route
    result = route(_make_state("some request"))
    assert result["route_to"] == "clarify"


@patch("core.supervisor._router_llm", _mock_llm_response('{"route": "unknown_route"}'))
def test_falls_back_to_clarify_on_unknown_route():
    from core.supervisor import route
    result = route(_make_state("some request"))
    assert result["route_to"] == "clarify"


def test_falls_back_to_clarify_on_empty_messages():
    # No LLM call expected — early-return path when messages is empty.
    from core.supervisor import route
    state = _make_state("ignored")
    state["messages"] = []
    result = route(state)
    assert result["route_to"] == "clarify"


@patch("core.supervisor._router_llm", _mock_llm_response('```json\n{"route": "meal_planner"}\n```'))
def test_strips_markdown_fences():
    from core.supervisor import route
    result = route(_make_state("Give me a weekly meal plan"))
    assert result["route_to"] == "meal_planner"
