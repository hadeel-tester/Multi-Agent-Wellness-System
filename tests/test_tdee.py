"""Unit tests for core/tdee.py — Mifflin-St Jeor TDEE calculator.

No API key required. Pure arithmetic only.

Run from project root:
    pytest tests/test_tdee.py -v
"""

import pytest

from core.tdee import calculate_tdee


# ---------------------------------------------------------------------------
# Test 1: Known male calculation
# ---------------------------------------------------------------------------

def test_male_moderate_maintain():
    """Hand-verified values for a standard male profile.

    BMR  = (10×80) + (6.25×180) − (5×30) + 5
         = 800 + 1125 − 150 + 5 = 1780.0
    TDEE = 1780.0 × 1.55 = 2759.0
    suggested_calories = round(2759.0 + 0) = 2759
    """
    result = calculate_tdee(
        weight_kg=80.0,
        height_cm=180.0,
        age=30,
        sex="Male",
        activity_level="moderate",
        goal="maintain",
    )

    assert result["bmr"] == pytest.approx(1780.0)
    assert result["tdee"] == pytest.approx(2759.0)
    assert result["suggested_calories"] == 2759
    assert result["activity_factor"] == pytest.approx(1.55)
    assert result["goal_adjustment"] == 0


# ---------------------------------------------------------------------------
# Test 2: Known female calculation
# ---------------------------------------------------------------------------

def test_female_light_maintain():
    """Hand-verified values for a standard female profile.

    BMR  = (10×65) + (6.25×165) − (5×28) − 161
         = 650 + 1031.25 − 140 − 161 = 1380.25
    TDEE = 1380.25 × 1.375 = 1897.84375 → rounded to 1897.84
    suggested_calories = round(1897.84375 + 0) = 1898
    """
    result = calculate_tdee(
        weight_kg=65.0,
        height_cm=165.0,
        age=28,
        sex="Female",
        activity_level="light",
        goal="maintain",
    )

    assert result["bmr"] == pytest.approx(1380.25)
    assert result["tdee"] == pytest.approx(1897.84, rel=1e-4)
    assert result["suggested_calories"] == 1898
    assert result["activity_factor"] == pytest.approx(1.375)
    assert result["goal_adjustment"] == 0


# ---------------------------------------------------------------------------
# Test 3: "Prefer not to say" returns average of male/female BMR
# ---------------------------------------------------------------------------

def test_prefer_not_to_say_is_average_of_sexes():
    """Sex constant for "Prefer not to say" is (5 + −161) / 2 = −78.

    For the same inputs, the returned BMR must equal the mean of the
    male-specific and female-specific BMRs.
    """
    kwargs = dict(
        weight_kg=75.0, height_cm=175.0, age=35,
        activity_level="moderate", goal="maintain",
    )
    male_bmr   = calculate_tdee(sex="Male",              **kwargs)["bmr"]
    female_bmr = calculate_tdee(sex="Female",            **kwargs)["bmr"]
    neutral_bmr = calculate_tdee(sex="Prefer not to say", **kwargs)["bmr"]

    assert neutral_bmr == pytest.approx((male_bmr + female_bmr) / 2)


# ---------------------------------------------------------------------------
# Test 4: All five activity levels produce strictly increasing TDEE
# ---------------------------------------------------------------------------

def test_activity_levels_produce_increasing_tdee():
    """Higher activity levels must yield higher TDEE values."""
    levels = ["sedentary", "light", "moderate", "active", "very_active"]
    kwargs = dict(weight_kg=70.0, height_cm=170.0, age=30, sex="Male", goal="maintain")

    tdee_values = [
        calculate_tdee(activity_level=lvl, **kwargs)["tdee"]
        for lvl in levels
    ]

    for i in range(len(tdee_values) - 1):
        assert tdee_values[i] < tdee_values[i + 1], (
            f"Expected TDEE({levels[i]}) < TDEE({levels[i+1]}), "
            f"got {tdee_values[i]} vs {tdee_values[i+1]}"
        )


# ---------------------------------------------------------------------------
# Test 5: Goal adjustments relative to "maintain"
# ---------------------------------------------------------------------------

def test_goal_adjustments_relative_to_maintain():
    """lose = maintain − 500; gain = maintain + 300."""
    kwargs = dict(
        weight_kg=80.0, height_cm=175.0, age=30,
        sex="Male", activity_level="moderate",
    )
    maintain = calculate_tdee(goal="maintain", **kwargs)["suggested_calories"]
    lose     = calculate_tdee(goal="lose",     **kwargs)["suggested_calories"]
    gain     = calculate_tdee(goal="gain",     **kwargs)["suggested_calories"]

    assert maintain - lose == 500
    assert gain - maintain == 300


def test_goal_adjustment_values_in_result():
    """The goal_adjustment field reflects the correct kcal delta."""
    base = dict(weight_kg=70.0, height_cm=170.0, age=25, sex="Female", activity_level="moderate")

    assert calculate_tdee(goal="lose",     **base)["goal_adjustment"] == -500
    assert calculate_tdee(goal="maintain", **base)["goal_adjustment"] == 0
    assert calculate_tdee(goal="gain",     **base)["goal_adjustment"] == 300


# ---------------------------------------------------------------------------
# Test 6: Minimum calorie floor of 1 200 kcal
# ---------------------------------------------------------------------------

def test_floor_prevents_suggested_calories_below_1200():
    """A tiny person on a "lose" goal must still receive at least 1 200 kcal.

    40 kg, 150 cm, 18 yo, Female, sedentary, lose:
      BMR  = (10×40) + (6.25×150) − (5×18) − 161 = 1086.5
      TDEE = 1086.5 × 1.2 = 1303.8
      raw  = round(1303.8 − 500) = 804  →  floored to 1200
    """
    result = calculate_tdee(
        weight_kg=40.0,
        height_cm=150.0,
        age=18,
        sex="Female",
        activity_level="sedentary",
        goal="lose",
    )

    assert result["bmr"] == pytest.approx(1086.5)
    assert result["suggested_calories"] == 1200


# ---------------------------------------------------------------------------
# Edge: invalid inputs raise ValueError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_kwarg,value", [
    ("sex",            "Unknown"),
    ("activity_level", "couch_potato"),
    ("goal",           "bulk"),
])
def test_invalid_inputs_raise_value_error(bad_kwarg, value):
    """Unrecognised enum-like inputs must raise ValueError immediately."""
    kwargs = dict(
        weight_kg=70.0, height_cm=170.0, age=30,
        sex="Male", activity_level="moderate", goal="maintain",
    )
    kwargs[bad_kwarg] = value
    with pytest.raises(ValueError):
        calculate_tdee(**kwargs)
