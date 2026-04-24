# Sprint 3 — Post-Review Fixes

## Fix 1: Calorie Calculation Bug (per 100g → actual portion)
- **Root cause:** System prompt told the agent to pass portion in grams but never said "use the actual serving size, not 100." With gpt-4o-mini at temperature=0, the model defaulted to 100g every time.
- **Fix:** Rewrote Step 3 in `prompts/system_prompts.py` to demand actual portion sizes with a worked example. Hardened `tools/nutrition_lookup.py` to default `amount_grams=0/None` to 100g instead of erroring, and standardized output to 1 decimal place.
- **Files changed:** `prompts/system_prompts.py`, `tools/nutrition_lookup.py`

## Fix 2: Shopping List — Realistic Quantities + Missing Units
- **Problem:** Shopping list extracted exact recipe amounts ("50g hummus", "30g granola") instead of purchasable quantities. Some items had no units at all.
- **Fix:** Updated the formatter prompt to convert recipe amounts to realistic supermarket quantities (round up to standard packs, always include quantity + unit).
- **Files changed:** `prompts/system_prompts.py`

## Fix 3: Dietary Warnings Not Prominent
- **Problem:** Allergen warnings appeared inline next to ingredient names, easily missed by users.
- **Fix:** Added ⚠️ Warnings block as a mandatory section immediately after meal name (before Ingredients) in the system prompt. Added `_render_plan_markdown()` in Streamlit to detect warning blocks and render them as `st.warning()` yellow alert boxes.
- **Files changed:** `prompts/system_prompts.py`, `app.py`

## Fix 4: Response Time + Output Quality
- **Problem:** gpt-4o-mini struggled with complex multi-step instructions (calorie targeting, format rules, self-checking). Output quality was inconsistent.
- **Fix:** Switched meal planner agent model from `gpt-4o-mini` to `gpt-4.1-mini`. Significantly improved instruction following and output consistency.
- **Files changed:** `core/graph.py` (model initialization)

## Fix 5: Full Agent Output Now Rendered
- **Problem:** Streamlit UI was stripping most of the agent's response — only showing meal name, ingredients, and kcal. Full macros, preparation hints, safety notes, and shopping list were discarded.
- **Fix:** Updated Streamlit rendering to display the complete agent response with proper markdown formatting.
- **Files changed:** `app.py`

## Fix 6: Agent No Longer Shows Draft/Revision Cycles
- **Problem:** Agent would output a draft plan, realize calories were short, then print an adjusted version — showing the user its internal thought process.
- **Fix:** Prompt now requires the agent to validate calories internally before writing the response. Explicit ban on "I will adjust" and before/after comparisons.
- **Files changed:** `prompts/system_prompts.py`

## Fix 7: Calorie Validation Node (Agentic Feedback Loop)
- **Problem:** Calorie target compliance relied entirely on prompt instructions — a soft constraint the LLM could ignore.
- **Fix:** Added `validate_calories` node to the LangGraph graph between `agent` and `format_output`. Parses "Day N total" line via regex, compares against user's calorie_target. If outside ±10%, routes back to agent with correction message. Capped at 2 retries. Added `calorie_retries` field to `AgentState`.
- **Architecture significance:** This is a programmatic constraint enforcement loop — the graph enforces what the prompt requests. Demonstrates genuine agentic behavior: automated feedback and self-correction without human intervention.
- **Files changed:** `core/graph.py`, `core/state.py`

## Fix 8: Macro Display Cleaned Up
- **Problem:** Agent showed per-ingredient arithmetic ("229.5 (eggs) + 33.3 (spinach) = 429.4 kcal") — too verbose for end users.
- **Fix:** Prompt now requires a single totals line per meal: "429.4 kcal | 32.1g protein | 27.3g carbs | 15.9g fat | 3.8g fiber"
- **Files changed:** `prompts/system_prompts.py`

## Fix 9: Expanded Nutrition Lookups (1 → 2-3 per meal)
- **Problem:** Agent only looked up one ingredient per meal in CIQUAL and estimated the rest "based on common knowledge" — undermining the app's data-driven value.
- **Fix:** Prompt now requires lookup for every calorie-significant ingredient (typically 2-3 per meal: protein, carb, fat sources). Only minor items (spices, herbs, small garnishes) can be estimated.
- **Files changed:** `prompts/system_prompts.py`
