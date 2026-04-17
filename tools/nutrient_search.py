# Source: Anses. 2025. Ciqual French food composition table. https://ciqual.anses.fr/

from langchain_core.tools import tool

from tools.nutrition_lookup import _df

_NUTRIENT_COLUMNS: dict[str, tuple[str, str]] = {
    "calories":   ("calories_per_100g", "kcal"),
    "protein":    ("protein_g",         "g protein"),
    "carbs":      ("carbs_g",           "g carbs"),
    "fat":        ("fat_g",             "g fat"),
    "fiber":      ("fiber_g",           "g fiber"),
    "iron":       ("iron_mg",           "mg iron"),
    "calcium":    ("calcium_mg",        "mg calcium"),
    "vitamin_c":  ("vitamin_c_mg",      "mg vitamin C"),
    "vitamin_d":  ("vitamin_d_ug",      "ug vitamin D"),
    "sodium":     ("sodium_mg",         "mg sodium"),
    "magnesium":  ("magnesium_mg",      "mg magnesium"),
}


@tool
def search_nutrient_foods(nutrient: str, food_group: str = "", top_n: str = "10") -> str:
    """
    Return the top foods from CIQUAL 2025 ranked by a given nutrient per 100 g.

    Use this tool when you need food-based swap suggestions for a nutrient gap —
    for example, finding high-protein foods to recommend when a meal plan is low
    on protein, or high-fiber options when fiber intake is insufficient.

    Valid nutrient names: "calories", "protein", "carbs", "fat", "fiber",
    "iron", "calcium", "vitamin_c", "vitamin_d", "sodium", "magnesium".
    Values come directly from CIQUAL tabular data — never from LLM knowledge.

    Args:
        nutrient:   One of "calories", "protein", "carbs", "fat", "fiber",
                    "iron", "calcium", "vitamin_c", "vitamin_d", "sodium",
                    "magnesium". Case-insensitive.
        food_group: Optional substring filter on the food_group column
                    (case-insensitive). Pass "" to search across all groups.
                    Examples: "poultry", "fish", "dairy", "legumes".
        top_n:      Number of results to return (as a string). Defaults to "10".

    Returns:
        A plain-text numbered list, one food per line:
        "1. Chicken, breast, grilled (poultry): 31.0g protein per 100g"
        Returns an error string if the nutrient name is not recognised.
    """
    key = nutrient.strip().lower()
    if key not in _NUTRIENT_COLUMNS:
        valid = ", ".join(sorted(_NUTRIENT_COLUMNS))
        return f"Error: '{nutrient}' is not a recognised nutrient. Valid options: {valid}."

    col, unit = _NUTRIENT_COLUMNS[key]

    try:
        n = max(1, int(top_n))
    except (ValueError, TypeError):
        n = 10

    df = _df.dropna(subset=[col])

    if food_group.strip():
        mask = df["food_group"].str.contains(food_group.strip(), case=False, na=False, regex=False)
        df = df[mask]
        if df.empty:
            return f"No foods found in food group matching '{food_group}'."

    top = df.nlargest(n, col)

    lines = []
    for rank, (_, row) in enumerate(top.iterrows(), start=1):
        value = round(float(row[col]), 1)
        name = row["food_name_en"]
        group = row["food_group"]
        lines.append(f"{rank}. {name} ({group}): {value}{unit} per 100g")

    if not lines:
        return f"No results found for nutrient '{nutrient}'."

    return "\n".join(lines)


if __name__ == "__main__":
    tests = [
        ("protein", "", "5"),
        ("fiber", "legume", "5"),
        ("fat", "fish", "5"),
        ("calories", "", "3"),
        ("vitamin_z", "", "5"),   # should return error
    ]
    for nut, grp, n in tests:
        print(f"\n--- search_nutrient_foods('{nut}', '{grp}', '{n}') ---")
        print(search_nutrient_foods.invoke({"nutrient": nut, "food_group": grp, "top_n": n}))
