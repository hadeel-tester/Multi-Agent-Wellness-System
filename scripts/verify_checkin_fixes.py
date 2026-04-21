"""Ad-hoc verification for the three check-in/variety fixes.

Invokes the supervisor with a 3-day meal plan request and a seeded
check_in_history. Prints counts of salmon/soup occurrences and whether any
two days share the same meal name.

Run from project root:
    python scripts/verify_checkin_fixes.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    print("OPENAI_API_KEY not set — aborting")
    sys.exit(1)

from core.graph import meal_agent  # noqa: E402

CHECK_IN_SUMMARY = (
    "User mostly followed the plan but found eating salmon twice per week boring. "
    "Energy was okay overall. They would like to have soup added about once every three days."
)

# Same check_in_context string the supervisor builds, inlined here so we can
# call the meal planner directly and bypass the supervisor's hardcoded
# recursion limit (MAX_ITERATIONS=80 is too tight for 3-day plans).
check_in_context = (
    f"[Previous check-in feedback — apply as soft preferences]\n"
    f"{CHECK_IN_SUMMARY}\n\n"
    f"Rules for applying feedback:\n"
    f"- 'Found X boring/repetitive' means include X at least once but no more than once "
    f"across the full plan. Do NOT drop X to zero — reducing is not eliminating.\n"
    f"- 'Would like Y added' means include Y roughly at the frequency the user suggested. "
    f"For example, 'once every three days' in a 4-day plan means 1 occurrence.\n"
    f"- These preferences do not override dietary restrictions, allergen rules, or calorie targets."
)

initial_state = {
    "messages": [HumanMessage(content="Plan 3 days of healthy meals for me")],
    "user_id": "verify_checkin_user",
    "user_profile": {
        "name": "Verify User",
        "age": 30,
        "health_goals": "weight loss",
        "calorie_target": 1800,
        "allergies": ["gluten"],
        "dietary_restrictions": ["halal"],
        "check_in_context": check_in_context,
    },
    "meal_plan": {},
    "shopping_list": [],
    "current_step": "start",
    "error": None,
}

print("Invoking meal_agent with 3-day request + seeded check-in context in profile...")
print(f"Check-in: {CHECK_IN_SUMMARY}\n")

result = meal_agent.invoke(
    initial_state,
    config={"recursion_limit": 160, "run_name": "verify_checkin_fixes"},
)

meal_plan = result.get("meal_plan", {})
err = result.get("error")

if err:
    print(f"ERROR: {err}")

print("=" * 60)
print("MEAL PLAN (by day / meal / name)")
print("=" * 60)

meal_names_by_day: dict[str, list[str]] = {}
for day, meals in meal_plan.items():
    meal_names_by_day[day] = []
    print(f"\n{day}:")
    if isinstance(meals, dict):
        for slot, details in meals.items():
            name = details.get("name", "(unnamed)") if isinstance(details, dict) else str(details)
            meal_names_by_day[day].append(name)
            print(f"  {slot.capitalize():10} {name}")

# ── Counts ──────────────────────────────────────────────────────────────────

all_names = [n.lower() for names in meal_names_by_day.values() for n in names]
salmon_count = sum(1 for n in all_names if "salmon" in n)
soup_count = sum(1 for n in all_names if "soup" in n)

print("\n" + "=" * 60)
print("CHECKS")
print("=" * 60)
print(f"Salmon occurrences:  {salmon_count}   (expected: exactly 1)")
print(f"Soup occurrences:    {soup_count}   (expected: 1 for 3-day plan — 'once every 3 days')")

# Day-duplication check: any two days sharing all three meal names?
day_tuples = {day: tuple(sorted(names)) for day, names in meal_names_by_day.items()}
dup_pairs = []
days = list(day_tuples.keys())
for i, d1 in enumerate(days):
    for d2 in days[i + 1:]:
        if day_tuples[d1] == day_tuples[d2]:
            dup_pairs.append((d1, d2))

# Per-meal duplication: e.g. same dinner on day 1 and day 3
per_slot_dups: list[str] = []
slot_to_names: dict[int, list[tuple[str, str]]] = {}
for day, names in meal_names_by_day.items():
    for idx, name in enumerate(names):
        slot_to_names.setdefault(idx, []).append((day, name.lower()))
for idx, pairs in slot_to_names.items():
    seen: dict[str, str] = {}
    for day, name in pairs:
        if name in seen:
            per_slot_dups.append(f"slot {idx}: {seen[name]} and {day} both = '{name}'")
        else:
            seen[name] = day

print(f"Identical days:      {len(dup_pairs)}   (expected: 0)  {dup_pairs if dup_pairs else ''}")
print(f"Per-slot duplicates: {len(per_slot_dups)}   (expected: 0)")
for d in per_slot_dups:
    print(f"    - {d}")

# ── Summary ─────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
passed = (
    salmon_count == 1
    and soup_count == 1
    and len(dup_pairs) == 0
    and len(per_slot_dups) == 0
)
print("PASS" if passed else "FAIL")
print("=" * 60)
