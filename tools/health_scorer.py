from langchain_core.tools import tool


def _parse_goals(health_goals_csv: str) -> list[str]:
    return [g.strip().lower() for g in health_goals_csv.split(",") if g.strip()]


@tool
def score_meal_health(
    calories: float,
    protein_g: float,
    carbs_g: float,
    fat_g: float,
    fiber_g: float,
    calorie_target: int,
    health_goals_csv: str,
) -> str:
    """
    Evaluate how well a meal fits the user's health goals and daily calorie target.

    Call this tool after computing macros for a meal (e.g. via lookup_nutrition) to
    get an objective quality score and an actionable improvement suggestion. This
    helps the agent decide whether to accept a proposed meal or adjust portion sizes
    and ingredient choices before presenting the plan to the user.

    Scoring is on a 1–10 scale. The baseline assumes one meal covers roughly one
    third of the user's daily calorie target. Penalties and bonuses are applied
    based on the macro breakdown and the user's stated health goals.

    Scoring criteria:
    - Calorie fit:      within ±20% of 33% of daily target → no penalty; outside → –1 to –2
    - Protein adequacy: ≥20g per meal → +1 bonus; <10g → –1 penalty
    - Fibre adequacy:   ≥5g per meal  → +1 bonus; <2g  → –1 penalty
    - Weight loss goal: calories >40% of daily target → additional –1 penalty
    - Heart health goal: fat <15g → +1 bonus

    Args:
        calories:         Total kilocalories in the meal (float).
        protein_g:        Total protein in grams (float).
        carbs_g:          Total carbohydrates in grams (float).
        fat_g:            Total fat in grams (float).
        fiber_g:          Total dietary fibre in grams (float).
        calorie_target:   User's total daily calorie target in kcal (int), e.g. 2000.
        health_goals_csv: Comma-separated health goals, e.g. "weight loss, heart health".
                          Recognised values: "weight loss", "heart health", "muscle gain",
                          "diabetes management". Unknown goals are accepted but have no
                          effect on scoring.

    Returns:
        A plain-text string containing:
        - The numeric score (1–10)
        - A brief reasoning summary
        - One concrete, actionable suggestion for improvement (or confirmation if excellent)
        Example:
        "Score: 7/10 | Protein is good (28g). Fibre is low (2g) and calories are slightly
        high for a weight-loss goal. Suggestion: add a side of leafy greens or legumes to
        boost fibre without significantly increasing calories."
    """
    goals = _parse_goals(health_goals_csv)
    meal_target = calorie_target / 3.0

    score = 5  # neutral baseline
    reasons: list[str] = []
    suggestion = ""

    # --- Calorie fit ---
    calorie_ratio = calories / meal_target if meal_target > 0 else 1.0
    if 0.8 <= calorie_ratio <= 1.2:
        reasons.append(f"calories are on target ({calories:.0f} kcal)")
    elif calorie_ratio < 0.8:
        score -= 1
        reasons.append(f"calories are low ({calories:.0f} kcal vs ~{meal_target:.0f} kcal target)")
    elif calorie_ratio <= 1.4:
        score -= 1
        reasons.append(f"calories are slightly high ({calories:.0f} kcal vs ~{meal_target:.0f} kcal target)")
    else:
        score -= 2
        reasons.append(f"calories are high ({calories:.0f} kcal vs ~{meal_target:.0f} kcal target)")

    # --- Weight loss penalty ---
    if "weight loss" in goals and calories > 0.4 * calorie_target:
        score -= 1
        reasons.append("exceeds 40% of daily target for weight loss goal")

    # --- Protein ---
    if protein_g >= 20:
        score += 1
        reasons.append(f"protein is good ({protein_g:.0f}g)")
    elif protein_g < 10:
        score -= 1
        reasons.append(f"protein is low ({protein_g:.0f}g)")
    else:
        reasons.append(f"protein is moderate ({protein_g:.0f}g)")

    # --- Fibre ---
    if fiber_g >= 5:
        score += 1
        reasons.append(f"fibre is good ({fiber_g:.0f}g)")
    elif fiber_g < 2:
        score -= 1
        reasons.append(f"fibre is low ({fiber_g:.0f}g)")
    else:
        reasons.append(f"fibre is moderate ({fiber_g:.0f}g)")

    # --- Heart health bonus ---
    if "heart health" in goals and fat_g < 15:
        score += 1
        reasons.append(f"fat is low ({fat_g:.0f}g), good for heart health")

    # Clamp to 1–10
    score = max(1, min(10, score))

    # --- Actionable suggestion ---
    if score >= 8:
        suggestion = "Meal looks well-balanced for your goals — no major changes needed."
    elif fiber_g < 2:
        suggestion = "Add leafy greens, legumes, or whole grains to boost fibre."
    elif protein_g < 10:
        suggestion = "Add a lean protein source (chicken, fish, legumes, or Greek yogurt)."
    elif "weight loss" in goals and calorie_ratio > 1.2:
        suggestion = "Reduce portion size or replace a high-calorie ingredient with vegetables."
    elif "heart health" in goals and fat_g >= 15:
        suggestion = "Swap saturated fats for olive oil or reduce added fats to support heart health."
    elif protein_g < 20:
        suggestion = "Increase protein slightly — aim for at least 20g per meal to support satiety."
    else:
        suggestion = "Consider adding a fibre-rich side to further improve the meal quality."

    reason_text = "; ".join(reasons).capitalize() + "."
    return (
        f"Score: {score}/10 | {reason_text} "
        f"Suggestion: {suggestion}"
    )


if __name__ == "__main__":
    tests = [
        # Good balanced meal for weight loss
        {
            "calories": 520.0,
            "protein_g": 35.0,
            "carbs_g": 45.0,
            "fat_g": 12.0,
            "fiber_g": 7.0,
            "calorie_target": 1800,
            "health_goals_csv": "weight loss",
        },
        # High calorie, low fibre — heart health goal
        {
            "calories": 950.0,
            "protein_g": 28.0,
            "carbs_g": 80.0,
            "fat_g": 45.0,
            "fiber_g": 1.5,
            "calorie_target": 2000,
            "health_goals_csv": "heart health",
        },
        # Low protein, moderate everything else
        {
            "calories": 400.0,
            "protein_g": 8.0,
            "carbs_g": 60.0,
            "fat_g": 10.0,
            "fiber_g": 4.0,
            "calorie_target": 2000,
            "health_goals_csv": "muscle gain",
        },
        # Excellent meal — high protein, high fibre, on-target calories
        {
            "calories": 650.0,
            "protein_g": 42.0,
            "carbs_g": 55.0,
            "fat_g": 11.0,
            "fiber_g": 9.0,
            "calorie_target": 2000,
            "health_goals_csv": "weight loss, heart health",
        },
    ]

    for t in tests:
        print(f"\nMeal: {t['calories']:.0f} kcal | {t['protein_g']:.0f}g protein | "
              f"{t['carbs_g']:.0f}g carbs | {t['fat_g']:.0f}g fat | {t['fiber_g']:.0f}g fiber")
        print(f"Target: {t['calorie_target']} kcal/day | Goals: {t['health_goals_csv']}")
        result = score_meal_health.invoke({k: v for k, v in t.items()})
        print(f"Result: {result}")
