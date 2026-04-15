"""TDEE (Total Daily Energy Expenditure) calculator using the Mifflin-St Jeor equation.

No external dependencies — pure Python arithmetic only.

Equations
---------
Mifflin-St Jeor BMR:
  Male:   BMR = (10 × weight_kg) + (6.25 × height_cm) − (5 × age) + 5
  Female: BMR = (10 × weight_kg) + (6.25 × height_cm) − (5 × age) − 161
  Prefer not to say: average of the two sex-specific constants → −78

TDEE = BMR × activity_factor

Suggested calories = TDEE + goal_adjustment, floored at 1 200 kcal.
"""

_ACTIVITY_FACTORS: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}

_GOAL_ADJUSTMENTS: dict[str, int] = {
    "lose": -500,
    "maintain": 0,
    "gain": 300,
}

# Mifflin-St Jeor sex constants (the term added after the shared base formula)
_SEX_CONSTANTS: dict[str, float] = {
    "Male": 5.0,
    "Female": -161.0,
    "Prefer not to say": (5.0 + -161.0) / 2,  # −78.0
}

_MIN_CALORIES = 1200


def calculate_tdee(
    weight_kg: float,
    height_cm: float,
    age: int,
    sex: str,
    activity_level: str,
    goal: str,
) -> dict:
    """Calculate BMR, TDEE, and a suggested daily calorie target.

    Uses the Mifflin-St Jeor equation for BMR and multiplies by an
    activity factor to arrive at TDEE.  A goal-based adjustment is then
    applied to produce a suggested calorie target, which is never allowed
    to fall below 1 200 kcal regardless of inputs.

    Args:
        weight_kg: Body weight in kilograms (e.g. 70.0).
        height_cm: Height in centimetres (e.g. 170.0).
        age: Age in whole years (e.g. 30).
        sex: Biological sex used for the BMR constant.  Accepted values:
            "Male", "Female", "Prefer not to say".
        activity_level: Self-reported activity level.  Accepted values:
            "sedentary", "light", "moderate", "active", "very_active".
        goal: Weight / health goal.  Accepted values:
            "lose" (−500 kcal), "maintain" (±0 kcal), "gain" (+300 kcal).

    Returns:
        A dict with the following keys:
            bmr (float):               Basal Metabolic Rate in kcal/day.
            tdee (float):              Total Daily Energy Expenditure in kcal/day.
            suggested_calories (int):  Recommended daily intake after goal
                                       adjustment; minimum 1 200 kcal.
            activity_factor (float):   Multiplier applied to BMR.
            goal_adjustment (int):     Kcal delta applied for the goal
                                       (−500, 0, or +300).

    Raises:
        ValueError: If sex, activity_level, or goal is not a recognised value.
    """
    if sex not in _SEX_CONSTANTS:
        raise ValueError(
            f"Unrecognised sex {sex!r}. "
            f"Expected one of: {list(_SEX_CONSTANTS)}"
        )
    if activity_level not in _ACTIVITY_FACTORS:
        raise ValueError(
            f"Unrecognised activity_level {activity_level!r}. "
            f"Expected one of: {list(_ACTIVITY_FACTORS)}"
        )
    if goal not in _GOAL_ADJUSTMENTS:
        raise ValueError(
            f"Unrecognised goal {goal!r}. "
            f"Expected one of: {list(_GOAL_ADJUSTMENTS)}"
        )

    sex_constant = _SEX_CONSTANTS[sex]
    activity_factor = _ACTIVITY_FACTORS[activity_level]
    goal_adjustment = _GOAL_ADJUSTMENTS[goal]

    bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) + sex_constant
    tdee = bmr * activity_factor
    suggested_calories = max(_MIN_CALORIES, round(tdee + goal_adjustment))

    return {
        "bmr": round(bmr, 2),
        "tdee": round(tdee, 2),
        "suggested_calories": suggested_calories,
        "activity_factor": activity_factor,
        "goal_adjustment": goal_adjustment,
    }
