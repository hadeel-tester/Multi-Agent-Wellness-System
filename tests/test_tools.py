"""Unit tests for all four LangChain tools.

Run from the project root:
    pytest tests/test_tools.py -v

No OpenAI API key required — the RAG validator fallback test patches _collection to None.
"""

import re
from unittest.mock import patch

import pytest

import tools.rag_validator as rag_module
from tools.allergen_checker import check_allergens
from tools.health_scorer import score_meal_health
from tools.nutrition_lookup import lookup_nutrition
from tools.rag_validator import validate_meal_safety


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_score(result: str) -> int:
    """Pull the numeric score from 'Score: N/10 | ...' output."""
    m = re.search(r"Score:\s*(\d+)/10", result)
    assert m, f"Score pattern not found in: {result!r}"
    return int(m.group(1))


def _extract_first_kcal(result: str) -> float:
    """Return the first kcal number found in a nutrition lookup result string."""
    m = re.search(r"([\d.]+)\s*kcal", result)
    assert m, f"kcal not found in: {result!r}"
    return float(m.group(1))


# ---------------------------------------------------------------------------
# nutrition_lookup
# ---------------------------------------------------------------------------


def test_nutrition_lookup_known_ingredient():
    """'chicken breast' is in COMMON_NAME_MAPPINGS and resolves to valid macros."""
    result = lookup_nutrition.invoke({"ingredient": "chicken breast", "amount_grams": 150.0})

    assert isinstance(result, str)
    assert "No food found" not in result, f"Unexpected not-found response: {result}"
    assert "kcal" in result
    assert "protein" in result


def test_nutrition_lookup_unknown_ingredient():
    """A nonsense ingredient name returns a helpful not-found message with suggestions."""
    result = lookup_nutrition.invoke({"ingredient": "xyzfood", "amount_grams": 100.0})

    assert isinstance(result, str)
    assert "No food found matching" in result
    assert "Closest alternatives" in result


def test_nutrition_lookup_scaling():
    """200 g should return approximately twice the kcal of 100 g for the same food."""
    result_100 = lookup_nutrition.invoke({"ingredient": "chicken breast", "amount_grams": 100.0})
    result_200 = lookup_nutrition.invoke({"ingredient": "chicken breast", "amount_grams": 200.0})

    kcal_100 = _extract_first_kcal(result_100)
    kcal_200 = _extract_first_kcal(result_200)

    ratio = kcal_200 / kcal_100
    assert abs(ratio - 2.0) < 0.05, (
        f"Expected ~2x scaling, got {ratio:.3f} "
        f"(100 g → {kcal_100} kcal, 200 g → {kcal_200} kcal)"
    )


# ---------------------------------------------------------------------------
# allergen_checker
# ---------------------------------------------------------------------------


def test_allergen_checker_safe():
    """Meal with no allergen overlap returns SAFE confirmation."""
    result = check_allergens.invoke(
        {
            "ingredients_csv": "chicken breast, jasmine rice, broccoli, olive oil",
            "user_allergies_csv": "gluten, dairy",
        }
    )

    assert result == "SAFE: No allergens detected."


def test_allergen_checker_warning():
    """Soy sauce in a meal triggers a WARNING for a user with a soy allergy."""
    result = check_allergens.invoke(
        {
            "ingredients_csv": "chicken, soy sauce, jasmine rice, sesame oil",
            "user_allergies_csv": "soy",
        }
    )

    assert "WARNING" in result
    assert "soy" in result.lower()


# ---------------------------------------------------------------------------
# health_scorer
# ---------------------------------------------------------------------------


def test_health_scorer_good_meal():
    """A balanced meal on-target for calories with good protein and fibre scores 7+."""
    # meal_target = 2000 / 3 ≈ 667 kcal
    # calorie_ratio = 650 / 667 ≈ 0.97 → in 0.8-1.2 band, no penalty  → 5
    # protein 30g ≥ 20g → +1                                            → 6
    # fiber 8g ≥ 5g → +1                                                → 7
    result = score_meal_health.invoke(
        {
            "calories": 650.0,
            "protein_g": 30.0,
            "carbs_g": 55.0,
            "fat_g": 15.0,
            "fiber_g": 8.0,
            "calorie_target": 2000,
            "health_goals_csv": "maintenance",
        }
    )

    assert "Score:" in result
    assert _extract_score(result) >= 7


def test_health_scorer_high_calorie_weight_loss():
    """A high-calorie meal for a weight-loss goal scores below 7."""
    # meal_target = 2000 / 3 ≈ 667 kcal
    # calorie_ratio = 1200 / 667 ≈ 1.8 → > 1.4, score -2              → 3
    # weight loss penalty: 1200 > 0.4 × 2000 = 800, score -1          → 2
    # protein 40g ≥ 20g → +1                                           → 3
    # fiber 6g ≥ 5g → +1                                               → 4
    result = score_meal_health.invoke(
        {
            "calories": 1200.0,
            "protein_g": 40.0,
            "carbs_g": 100.0,
            "fat_g": 50.0,
            "fiber_g": 6.0,
            "calorie_target": 2000,
            "health_goals_csv": "weight loss",
        }
    )

    assert "Score:" in result
    assert _extract_score(result) < 7


# ---------------------------------------------------------------------------
# rag_validator
# ---------------------------------------------------------------------------


def test_rag_validator_graceful_fallback():
    """When ChromaDB is unavailable (_collection=None), the tool returns a
    clear fallback message instead of raising an exception."""
    with patch.object(rag_module, "_collection", None), patch.object(
        rag_module, "_embeddings", None
    ):
        result = validate_meal_safety.invoke(
            {
                "meal_description": "grilled chicken breast with steamed broccoli and brown rice",
                "user_context": "no allergies; goal: weight loss",
            }
        )

    assert isinstance(result, str)
    assert len(result) > 0
    # The fallback message mentions the KB being unavailable
    assert "unavailable" in result.lower() or "knowledge base" in result.lower()
