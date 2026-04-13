# Source: Anses. 2025. Ciqual French food composition table. https://ciqual.anses.fr/

from pathlib import Path
import difflib

import pandas as pd
import numpy as np
from langchain_core.tools import tool

_CSV_PATH = Path(__file__).parent.parent / "data" / "ciqual" / "ciqual_cleaned.csv"
_df: pd.DataFrame = pd.read_csv(_CSV_PATH, dtype={"alim_code": str})

COMMON_NAME_MAPPINGS: dict[str, str] = {
    "brown rice": "rice wholegrain",
    "whole wheat bread": "bread wholemeal",
    "whole grain bread": "bread wholemeal",
    "olive oil": "olive oil",
    "greek yogurt": "yogurt greek",
    "ground beef": "beef minced",
    "chicken breast": "Chicken, breast, without skin, raw",
    "whole milk": "milk whole",
    "skim milk": "milk skimmed",
    "sweet potato": "sweet potato",
    "peanut butter": "peanut butter",
    "oats": "oat flakes",
    "cream cheese": "cheese cream",
    "cottage cheese": "cheese cottage",
}


def _fmt(value, unit: str) -> str:
    """Format a nutritional value, returning 'unknown' for NaN."""
    if pd.isna(value):
        return f"unknown {unit}"
    # Round to reasonable precision: drop trailing .0 for whole numbers
    rounded = round(float(value), 1)
    if rounded == int(rounded):
        return f"{int(rounded)}{unit}"
    return f"{rounded}{unit}"


def _scale(value, factor: float):
    """Scale a per-100g value by factor. Returns NaN if value is NaN."""
    if pd.isna(value):
        return float("nan")
    return float(value) * factor


def _format_row(row: pd.Series, amount_grams: float) -> str:
    """Return a human-readable nutrition string for one food row."""
    factor = amount_grams / 100.0
    name = row["food_name_en"]
    group = row["food_group"]
    kcal = _scale(row["calories_per_100g"], factor)
    protein = _scale(row["protein_g"], factor)
    carbs = _scale(row["carbs_g"], factor)
    fat = _scale(row["fat_g"], factor)
    fiber = _scale(row["fiber_g"], factor)

    return (
        f"{amount_grams:g}g {name} ({group}): "
        f"{_fmt(kcal, ' kcal')}, "
        f"{_fmt(protein, 'g protein')}, "
        f"{_fmt(carbs, 'g carbs')}, "
        f"{_fmt(fat, 'g fat')}, "
        f"{_fmt(fiber, 'g fiber')}"
    )


def _closest_names(ingredient: str, n: int = 5) -> list[str]:
    """
    Return up to n food names that are closest to the ingredient query.
    First tries per-word substring matching; falls back to SequenceMatcher.
    """
    query_lower = ingredient.lower()
    query_words = [w for w in query_lower.split() if len(w) > 2]
    names = _df["food_name_en"].tolist()
    names_lower = [n.lower() for n in names]

    # Score by number of query words found in the food name
    scored = []
    for i, nl in enumerate(names_lower):
        hits = sum(1 for w in query_words if w in nl)
        if hits > 0:
            scored.append((hits, len(names[i]), names[i]))

    if scored:
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [name for _, _, name in scored[:n]]

    # Fallback: difflib close matches against all names
    close = difflib.get_close_matches(ingredient, names, n=n, cutoff=0.3)
    if close:
        return close

    # Last resort: return the n lexicographically closest names
    return sorted(names, key=lambda x: difflib.SequenceMatcher(None, query_lower, x.lower()).ratio(), reverse=True)[:n]


@tool
def lookup_nutrition(ingredient: str, amount_grams: float) -> str:
    """
    Look up exact nutritional values from the CIQUAL 2025 database (3,484 foods).

    Use this tool to get precise macros — calories, protein, carbohydrates, fat, and
    fibre — for any ingredient in a meal plan. Values are sourced directly from the
    official French government food composition database (Anses, 2025 edition) and
    scaled to the requested portion size.

    When to call:
    - Any time you need accurate macro data for a specific ingredient or food.
    - Before finalising a meal plan's nutritional totals.
    - When checking whether a food fits a user's calorie or macro targets.

    Search behaviour:
    - Case-insensitive substring match on English food names.
    - If multiple foods match, the 3 most specific results (shortest name) are returned
      so you can pick the most appropriate one and call the tool again if needed.
    - If no match is found, the tool returns the 5 closest alternatives so you can
      retry with a more precise query.

    Args:
        ingredient: Common English name of the food (e.g. "chicken breast", "brown rice",
                    "whole milk", "olive oil"). Avoid brand names — use generic names.
        amount_grams: Portion weight in grams (e.g. 150.0 for a 150 g serving).

    Returns:
        A plain-text string with the scaled nutrition facts, e.g.:
        "150g Chicken, breast, grilled (poultry): 248 kcal, 46.5g protein, 0g carbs, 5.4g fat, 0g fiber"
        If a value is missing from the database, it is reported as "unknown" rather than zero.
    """
    if not ingredient or not ingredient.strip():
        return "Error: ingredient name cannot be empty."
    if amount_grams <= 0:
        return "Error: amount_grams must be greater than 0."

    ingredient = COMMON_NAME_MAPPINGS.get(ingredient.strip().lower(), ingredient)

    words = ingredient.strip().lower().split()
    names_lower = _df["food_name_en"].str.lower()
    mask = pd.Series([True] * len(_df), index=_df.index)
    for word in words:
        mask &= names_lower.str.contains(word, na=False)
    matches = _df[mask].copy()

    if matches.empty:
        closest = _closest_names(ingredient)
        suggestions = "\n".join(f"  - {n}" for n in closest)
        return (
            f"No food found matching '{ingredient}' in the CIQUAL 2025 database.\n"
            f"Closest alternatives (retry with one of these):\n{suggestions}"
        )

    # Sort by name length ascending (shorter = more specific match)
    matches["_name_len"] = matches["food_name_en"].str.len()
    matches = matches.sort_values("_name_len").head(3)

    lines = []
    for _, row in matches.iterrows():
        lines.append(_format_row(row, amount_grams))

    if len(lines) == 1:
        return lines[0]

    result = f"Multiple matches for '{ingredient}' — showing top {len(lines)} (most specific first):\n"
    result += "\n".join(f"  {i + 1}. {line}" for i, line in enumerate(lines))
    return result


if __name__ == "__main__":
    tests = [
        ("chicken", 150.0),
        ("brown rice", 200.0),
        ("salmon", 100.0),
        ("xyznonexistent", 100.0),
    ]
    for ingredient, grams in tests:
        print(f"\n--- lookup_nutrition('{ingredient}', {grams}) ---")
        print(lookup_nutrition.invoke({"ingredient": ingredient, "amount_grams": grams}))
