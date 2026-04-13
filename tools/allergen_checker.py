from langchain_core.tools import tool

# Maps ingredient keywords to the allergen category they contain.
# Key: substring that may appear in an ingredient name (lowercase).
# Value: allergen category name.
_INGREDIENT_ALLERGEN_MAP: dict[str, str] = {
    "soy sauce": "soy",
    "tofu": "soy",
    "tempeh": "soy",
    "edamame": "soy",
    "miso": "soy",
    "bread": "gluten",
    "wheat": "gluten",
    "flour": "gluten",
    "pasta": "gluten",
    "couscous": "gluten",
    "barley": "gluten",
    "rye": "gluten",
    "seitan": "gluten",
    "cheese": "dairy",
    "milk": "dairy",
    "butter": "dairy",
    "cream": "dairy",
    "yogurt": "dairy",
    "yoghurt": "dairy",
    "whey": "dairy",
    "egg": "eggs",
    "mayonnaise": "eggs",
    "aioli": "eggs",
    "peanut": "peanuts",
    "almond": "tree nuts",
    "cashew": "tree nuts",
    "walnut": "tree nuts",
    "pecan": "tree nuts",
    "pistachio": "tree nuts",
    "hazelnut": "tree nuts",
    "shrimp": "shellfish",
    "prawn": "shellfish",
    "crab": "shellfish",
    "lobster": "shellfish",
    "crayfish": "shellfish",
    "scallop": "shellfish",
    "clam": "shellfish",
    "oyster": "shellfish",
    "salmon": "fish",
    "tuna": "fish",
    "cod": "fish",
    "anchovy": "fish",
    "anchovies": "fish",
    "sardine": "fish",
    "mackerel": "fish",
    "sesame": "sesame",
    "tahini": "sesame",
    "mustard": "mustard",
    "celery": "celery",
    "lupin": "lupin",
    "sulphite": "sulphites",
    "sulfite": "sulphites",
    "wine": "sulphites",
    "vinegar": "sulphites",
}


def _parse_csv(value: str) -> list[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]


@tool
def check_allergens(ingredients_csv: str, user_allergies_csv: str) -> str:
    """
    Check whether a meal's ingredients conflict with the user's known allergies.

    Always call this tool when the user has listed one or more allergies in their
    health profile, before finalising any meal plan. It compares each ingredient
    against a built-in allergen mapping and against the user's stated allergy list
    directly (in case an allergen keyword appears verbatim in an ingredient name).

    The tool uses a broad matching approach: if an allergen keyword appears anywhere
    inside an ingredient name it is flagged. This errs on the side of caution —
    better a false positive than a missed allergy.

    Args:
        ingredients_csv: Comma-separated list of ingredients in the meal, e.g.
                         "chicken breast, soy sauce, jasmine rice, sesame oil"
        user_allergies_csv: Comma-separated list of the user's allergens, e.g.
                            "gluten, soy, shellfish"

    Returns:
        "SAFE: No allergens detected." if no conflicts are found.
        One or more "WARNING:" lines describing each conflict, e.g.:
        "WARNING: soy sauce contains soy (user allergy)"
        Multiple warnings are returned as separate lines.
    """
    ingredients = _parse_csv(ingredients_csv)
    allergies = _parse_csv(user_allergies_csv)

    if not ingredients:
        return "SAFE: No ingredients provided."
    if not allergies:
        return "SAFE: No allergies listed for user."

    warnings: list[str] = []

    for ingredient in ingredients:
        # Check via the allergen mapping table
        for keyword, allergen in _INGREDIENT_ALLERGEN_MAP.items():
            if keyword in ingredient and allergen in allergies:
                warnings.append(
                    f"WARNING: '{ingredient}' contains {allergen} (user allergy)"
                )
                break  # one warning per ingredient is enough
        else:
            # Also check if any allergy keyword appears directly in the ingredient name
            for allergy in allergies:
                if allergy in ingredient:
                    warnings.append(
                        f"WARNING: '{ingredient}' contains {allergy} (user allergy)"
                    )
                    break

    if not warnings:
        return "SAFE: No allergens detected."
    return "\n".join(warnings)


if __name__ == "__main__":
    tests = [
        # Should flag soy (soy sauce) and gluten (bread)
        (
            "chicken breast, soy sauce, brown bread, jasmine rice",
            "gluten, soy, shellfish",
        ),
        # Safe meal
        (
            "salmon fillet, olive oil, broccoli, sweet potato",
            "gluten, dairy",
        ),
        # Fish allergy — salmon direct match via mapping
        (
            "salmon fillet, lemon juice, capers",
            "fish",
        ),
        # No allergies listed
        (
            "pasta, cheese, cream",
            "",
        ),
        # Shellfish allergy
        (
            "garlic, olive oil, shrimp, parsley",
            "shellfish",
        ),
    ]

    for ingredients, allergies in tests:
        print(f"\nIngredients : {ingredients}")
        print(f"Allergies   : {allergies or '(none)'}")
        result = check_allergens.invoke(
            {"ingredients_csv": ingredients, "user_allergies_csv": allergies}
        )
        print(f"Result      : {result}")
