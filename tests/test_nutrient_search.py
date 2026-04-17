"""Unit tests for the search_nutrient_foods tool.

Run from the project root:
    pytest tests/test_nutrient_search.py -v

No API key required — the tool reads only the local CIQUAL CSV.
"""

import re

import pytest

from tools.nutrient_search import search_nutrient_foods


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_value(line: str, unit_fragment: str) -> float:
    """Extract the numeric nutrient value from a result line."""
    m = re.search(rf"([\d.]+){re.escape(unit_fragment)}", line)
    assert m, f"Expected '{unit_fragment}' value in line: {line!r}"
    return float(m.group(1))


def _lines(result: str) -> list[str]:
    return [l.strip() for l in result.strip().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_protein_returns_five_results():
    result = search_nutrient_foods.invoke({"nutrient": "protein", "food_group": "", "top_n": "5"})
    rows = _lines(result)
    assert len(rows) == 5, f"Expected 5 results, got {len(rows)}:\n{result}"
    for row in rows:
        assert "protein" in row, f"'protein' missing in row: {row!r}"
        assert "per 100g" in row, f"'per 100g' missing in row: {row!r}"


def test_fiber_vegetables_returns_three_results_in_correct_group():
    result = search_nutrient_foods.invoke({"nutrient": "fiber", "food_group": "vegetables", "top_n": "3"})
    rows = _lines(result)
    assert len(rows) == 3, f"Expected 3 results, got {len(rows)}:\n{result}"
    for row in rows:
        # food_group is shown inside parentheses: "Name (group): ..."
        m = re.search(r"\(([^)]+)\)", row)
        assert m, f"No food group in parentheses found in row: {row!r}"
        assert "vegetables" in m.group(1).lower(), (
            f"Food group '{m.group(1)}' does not contain 'vegetables' in row: {row!r}"
        )


def test_invalid_nutrient_returns_error_with_valid_options():
    result = search_nutrient_foods.invoke({"nutrient": "invalid_nutrient", "food_group": "", "top_n": "5"})
    assert "error" in result.lower() or "Error" in result, (
        f"Expected an error message, got: {result!r}"
    )
    for valid in ("calories", "protein", "carbs", "fat", "fiber"):
        assert valid in result, f"Valid nutrient '{valid}' not listed in error: {result!r}"


def test_fat_results_sorted_descending():
    result = search_nutrient_foods.invoke({"nutrient": "fat", "food_group": "", "top_n": "10"})
    rows = _lines(result)
    assert len(rows) >= 2, f"Expected at least 2 results, got {len(rows)}:\n{result}"
    values = [_parse_value(row, "g fat") for row in rows]
    assert values[0] >= values[-1], (
        f"Results not in descending order: first={values[0]}, last={values[-1]}"
    )
    # Verify full strict non-increasing order
    for i in range(len(values) - 1):
        assert values[i] >= values[i + 1], (
            f"Out of order at position {i}: {values[i]} < {values[i + 1]}\n{result}"
        )
