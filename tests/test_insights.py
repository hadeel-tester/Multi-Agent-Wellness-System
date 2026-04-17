"""Unit tests for the nutritional insights agent (agents/insights/graph.py).

Tests focus on the pure data-transformation nodes (prepare_context,
format_insights) — no LLM calls are made, no API key is required.

Supervisor routing for insights messages is already covered by
test_supervisor.py::test_routes_nutrient_gap_question.

Run from project root:
    pytest tests/test_insights.py -v
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SAMPLE_MEAL_PLAN = {
    "day_1": {
        "breakfast": {
            "name": "Scrambled eggs on toast",
            "calories": 420,
            "protein_g": 22,
            "carbs_g": 30,
            "fat_g": 22,
            "fiber_g": 1.5,
        },
        "lunch": {
            "name": "Grilled chicken with rice",
            "calories": 580,
            "protein_g": 50,
            "carbs_g": 65,
            "fat_g": 10,
            "fiber_g": 1.2,
        },
        "dinner": {
            "name": "Salmon with mashed potato",
            "calories": 620,
            "protein_g": 38,
            "carbs_g": 40,
            "fat_g": 30,
            "fiber_g": 3.0,
        },
    }
}
# Expected day totals: 1620 kcal | 110g protein | 135g carbs | 62g fat | 5.7g fiber


def _make_state(meal_plan=None, user_profile=None, messages=None):
    return {
        "messages": messages or [],
        "user_profile": user_profile or {},
        "meal_plan": meal_plan if meal_plan is not None else {},
        "nutrient_gaps": [],
        "suggestions": [],
        "summary": "",
        "error": None,
    }


# Realistic agent output that exercises all three regex patterns.
_AGENT_RESPONSE = """\
## Nutritional Gap Analysis

**Gaps Identified**

- Fiber: 5.7g/day average vs. 25g reference (77.2% below) ⚠️
- Iron: 8.3mg/day average vs. 14mg reference (40.7% below) ⚠️

**Food-Based Swap Suggestions**

- To increase fiber: lentils (7.9g/100g), chickpeas (7.6g/100g), oats (10.6g/100g)
- To increase iron: lentils (3.3mg/100g), spinach (2.7mg/100g)

**Summary**

Your meal plan is low in fiber and iron. Consider adding more legumes and leafy greens.

*General wellness information based on food composition data. Not medical advice.*
"""


# ---------------------------------------------------------------------------
# prepare_context — formatting tests (no LLM call)
# ---------------------------------------------------------------------------

class TestPrepareContext:
    def test_formats_known_plan_with_macro_totals(self):
        from agents.insights.graph import prepare_context

        result = prepare_context(_make_state(meal_plan=SAMPLE_MEAL_PLAN))

        assert len(result["messages"]) == 1
        msg = result["messages"][0]
        assert isinstance(msg, HumanMessage)
        content = msg.content

        # Day heading
        assert "Day 1" in content
        # All three meal slots
        assert "Breakfast" in content
        assert "Lunch" in content
        assert "Dinner" in content
        # Meal names
        assert "Scrambled eggs on toast" in content
        assert "Grilled chicken with rice" in content
        assert "Salmon with mashed potato" in content
        # Calorie total (420 + 580 + 620 = 1620)
        assert "1620" in content

    def test_empty_meal_plan_short_circuits_with_ai_message(self):
        from agents.insights.graph import prepare_context, EMPTY_PLAN_MESSAGE

        result = prepare_context(_make_state(meal_plan={}))

        assert len(result["messages"]) == 1
        msg = result["messages"][0]
        assert isinstance(msg, AIMessage)
        assert EMPTY_PLAN_MESSAGE in msg.content
        assert result["nutrient_gaps"] == []
        assert result["suggestions"] == []

    def test_includes_profile_values_in_context(self):
        from agents.insights.graph import prepare_context

        profile = {
            "calorie_target": 1800,
            "health_goals": "heart health",
            "dietary_restrictions": ["Vegan"],
            "allergies": ["peanuts"],
        }
        result = prepare_context(_make_state(meal_plan=SAMPLE_MEAL_PLAN, user_profile=profile))

        content = result["messages"][0].content
        assert "1800" in content
        assert "Vegan" in content
        assert "peanuts" in content


# ---------------------------------------------------------------------------
# format_insights — regex parsing tests (no LLM call)
# ---------------------------------------------------------------------------

class TestFormatInsights:
    def _state_with(self, text: str) -> dict:
        return _make_state(
            meal_plan=SAMPLE_MEAL_PLAN,
            messages=[AIMessage(content=text)],
        )

    def test_extracts_two_nutrient_gaps(self):
        from agents.insights.graph import format_insights

        result = format_insights(self._state_with(_AGENT_RESPONSE))

        gaps = result["nutrient_gaps"]
        assert len(gaps) == 2

    def test_fiber_gap_fields_are_correct(self):
        from agents.insights.graph import format_insights

        result = format_insights(self._state_with(_AGENT_RESPONSE))

        fiber = next(g for g in result["nutrient_gaps"] if g["nutrient"] == "Fiber")
        assert fiber["current_avg"] == pytest.approx(5.7)
        assert fiber["reference"] == pytest.approx(25.0)
        assert fiber["gap_pct"] == pytest.approx(77.2)
        assert fiber["direction"] == "below"
        assert fiber["flagged"] is True

    def test_extracts_food_swap_suggestions(self):
        from agents.insights.graph import format_insights

        result = format_insights(self._state_with(_AGENT_RESPONSE))

        suggestions = result["suggestions"]
        assert len(suggestions) == 2

        fiber_sug = next(s for s in suggestions if s["gap"] == "fiber")
        food_names = [f["food"] for f in fiber_sug["food_swaps"]]
        # Check primary foods are present (regex may prepend ", " to non-first items)
        assert any("lentils" in n for n in food_names)
        assert any("oats" in n for n in food_names)

    def test_extracts_summary_text(self):
        from agents.insights.graph import format_insights

        result = format_insights(self._state_with(_AGENT_RESPONSE))

        summary = result["summary"]
        assert "fiber" in summary.lower()
        assert "iron" in summary.lower()

    def test_empty_response_sets_error(self):
        from agents.insights.graph import format_insights

        result = format_insights(self._state_with(""))

        assert result["error"] is not None
        assert result["nutrient_gaps"] == []

    def test_unstructured_prose_stored_as_summary(self):
        """When the LLM returns prose without the expected structure, the raw
        text is stored in summary so the user still sees a useful response."""
        from agents.insights.graph import format_insights

        plain = "Your plan looks generally balanced but could use more vegetables."
        result = format_insights(self._state_with(plain))

        assert result["summary"] == plain
        assert result["nutrient_gaps"] == []
        assert result["suggestions"] == []
        assert result["error"] is None
