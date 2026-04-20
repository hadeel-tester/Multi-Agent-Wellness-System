"""Unit tests for the Check-In agent and related memory functions.

Tests cover:
- SQLite persistence via save_check_in / load_recent_check_ins
- prepare_context node behaviour (no LLM, no API key required)
- Supervisor routing for check-in intent (mocked LLM)

Run from project root:
    pytest tests/test_checkin.py -v
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """Point MEMORY_DB_PATH at a fresh temp file for each test."""
    db_file = tmp_path / "test_checkins.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(db_file))
    # Re-import after env var is set so _get_db_path() picks up the new value.
    from core.memory import init_db
    init_db()
    return str(db_file)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_supervisor_state(user_text: str) -> dict:
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


def _mock_router(json_payload: str):
    mock_response = MagicMock()
    mock_response.content = json_payload
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = mock_response
    return mock_llm


# ---------------------------------------------------------------------------
# 1. save_check_in stores data and load_recent_check_ins retrieves it
# ---------------------------------------------------------------------------

def test_save_and_load_check_in(tmp_db):
    from core.memory import save_check_in, load_recent_check_ins

    save_check_in("alice", {
        "adherence": "mostly",
        "problem_meals": "lentil soup too heavy",
        "energy_level": "okay",
        "weight_kg": 65.5,
        "notes": "Prefers quicker dinners.",
    })

    rows = load_recent_check_ins("alice", limit=5)
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == "alice"
    assert row["adherence"] == "mostly"
    assert row["problem_meals"] == "lentil soup too heavy"
    assert row["energy_level"] == "okay"
    assert abs(row["weight_kg"] - 65.5) < 0.01
    assert row["notes"] == "Prefers quicker dinners."
    assert row["created_at"]  # non-empty ISO timestamp


# ---------------------------------------------------------------------------
# 2. load_recent_check_ins returns empty list when no check-ins exist
# ---------------------------------------------------------------------------

def test_load_check_ins_empty(tmp_db):
    from core.memory import load_recent_check_ins

    rows = load_recent_check_ins("nobody")
    assert rows == []


# ---------------------------------------------------------------------------
# 3. load_recent_check_ins respects the limit parameter
# ---------------------------------------------------------------------------

def test_load_check_ins_respects_limit(tmp_db):
    from core.memory import save_check_in, load_recent_check_ins

    for i in range(3):
        save_check_in("bob", {"notes": f"check-in {i + 1}"})

    rows = load_recent_check_ins("bob", limit=2)
    assert len(rows) == 2
    # Most recent first — notes should be "check-in 3" and "check-in 2"
    assert rows[0]["notes"] == "check-in 3"
    assert rows[1]["notes"] == "check-in 2"


# ---------------------------------------------------------------------------
# 4. prepare_context handles missing previous check-ins gracefully
# ---------------------------------------------------------------------------

def test_prepare_context_first_time_user(tmp_db):
    from agents.checkin.graph import prepare_context

    state = {
        "messages": [HumanMessage(content="I'd like to check in.")],
        "user_profile": {
            "health_goals": "balanced nutrition",
            "dietary_restrictions": ["halal"],
            "allergies": ["peanuts"],
        },
        "user_id": "newuser",
        "check_in_data": {},
        "summary": "",
        "error": None,
    }
    result = prepare_context(state)

    assert "messages" in result
    assert len(result["messages"]) == 1
    context = result["messages"][0].content
    assert "first check-in" in context
    assert "balanced nutrition" in context
    assert "halal" in context
    assert "peanuts" in context


def test_prepare_context_returning_user(tmp_db):
    from core.memory import save_check_in
    from agents.checkin.graph import prepare_context

    save_check_in("carol", {"notes": "Preferred lighter lunches last time."})

    state = {
        "messages": [HumanMessage(content="Weekly check-in.")],
        "user_profile": {},
        "user_id": "carol",
        "check_in_data": {},
        "summary": "",
        "error": None,
    }
    result = prepare_context(state)
    context = result["messages"][0].content
    assert "Last time you mentioned" in context
    assert "Preferred lighter lunches last time." in context


# ---------------------------------------------------------------------------
# 5. Supervisor routes "I want to check in" to check_in
# ---------------------------------------------------------------------------

@patch("core.supervisor._router_llm", _mock_router('{"route": "check_in"}'))
def test_supervisor_routes_check_in_request():
    from core.supervisor import route

    result = route(_make_supervisor_state("I want to check in"))
    assert result["route_to"] == "check_in"


# ---------------------------------------------------------------------------
# 6. Supervisor routes "weekly feedback" to check_in
# ---------------------------------------------------------------------------

@patch("core.supervisor._router_llm", _mock_router('{"route": "check_in"}'))
def test_supervisor_routes_weekly_feedback():
    from core.supervisor import route

    result = route(_make_supervisor_state("weekly feedback on my meals"))
    assert result["route_to"] == "check_in"
