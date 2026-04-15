"""Manual end-to-end test — invokes the full LangGraph meal planning agent.

Requires a valid OPENAI_API_KEY in .env or the environment.
Skipped automatically when the key is absent.

Run:
    pytest tests/test_e2e.py -v -s         # -s to see printed output
    pytest tests/test_e2e.py -v -s --timeout=300   # extend timeout if needed
"""

import os

import pytest
from dotenv import load_dotenv

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    pytest.skip("OPENAI_API_KEY not set — skipping e2e test", allow_module_level=True)

from langchain_core.messages import HumanMessage  # noqa: E402 — after skip guard

from core.graph import MAX_ITERATIONS, meal_agent  # noqa: E402


def test_e2e_meal_plan():
    """Full agent invocation: asserts non-empty meal_plan and shopping_list."""
    user_id = "test_e2e_user"
    initial_state = {
        "messages": [HumanMessage(content="Plan 3 days of healthy meals for me")],
        "user_id": user_id,
        "user_profile": {
            "name": "E2E Test User",
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
    }
    config = {
        "recursion_limit": MAX_ITERATIONS,
        "run_name": "meal_plan_generation",
        "metadata": {"user_id": user_id, "sprint": "sprint3"},
    }

    result = meal_agent.invoke(initial_state, config=config)

    # ── Assertions ─────────────────────────────────────────────────────────

    assert result.get("error") is None, f"Agent returned error: {result['error']}"

    meal_plan = result.get("meal_plan", {})
    assert isinstance(meal_plan, dict), "meal_plan should be a dict"
    assert len(meal_plan) > 0, "meal_plan is empty — agent produced no structured output"

    shopping_list = result.get("shopping_list", [])
    assert isinstance(shopping_list, list), "shopping_list should be a list"
    assert len(shopping_list) > 0, "shopping_list is empty"

    # ── Manual inspection output ────────────────────────────────────────────

    print("\n" + "=" * 70)
    print("E2E TEST — AGENT OUTPUT")
    print("=" * 70)

    print("\n--- Messages ---")
    for msg in result["messages"]:
        label = type(msg).__name__
        content = msg.content if msg.content else f"[tool_calls: {getattr(msg, 'tool_calls', [])}]"
        text = str(content)
        print(f"{label}: {text[:300]}{'...' if len(text) > 300 else ''}\n")

    print("\n--- Meal Plan ---")
    for day, meals in meal_plan.items():
        print(f"\n{day}:")
        if isinstance(meals, dict):
            for meal_name, details in meals.items():
                print(f"  {meal_name.capitalize()}:")
                if isinstance(details, dict):
                    print(f"    {details.get('name', '(unnamed)')}")
                    if details.get("calories"):
                        print(f"    {details['calories']} kcal")
                else:
                    print(f"    {details}")

    print(f"\n--- Shopping List ({len(shopping_list)} items) ---")
    for item in shopping_list:
        print(f"  - {item}")

    if result.get("error"):
        print(f"\n--- Error ---\n{result['error']}")
