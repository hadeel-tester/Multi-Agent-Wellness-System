# Ethical Assessment — NutriMind AI Wellness Coach

*Capstone submission, April 2026. Companion document to [README.md](../README.md) and [CLAUDE.md](../CLAUDE.md).*

This assessment documents the ethical considerations, liability boundaries, and design constraints embedded in NutriMind. Each section references concrete implementation decisions rather than aspirational principles — the goal is to show how ethical constraints are enforced in code and prompts, not only described in documentation.

---

## 1. Scope of Advice

NutriMind provides **general wellness information based on food composition data**. It is not a medical device, does not diagnose, and does not replace professional dietary, medical, or psychological advice. The system is designed for generally healthy adults who want evidence-based meal planning grounded in an authoritative food database — not for users managing a diagnosed medical condition.

Every agent system prompt in [prompts/system_prompts.py](../prompts/system_prompts.py) ends with a general wellness disclaimer. The Streamlit sidebar carries a persistent on-screen caption: *"NutriMind provides general wellness information. Not a substitute for professional dietary advice."* (see Capstone 21 / 27 in [CHANGELOG_CAPSTONE.md](../CHANGELOG_CAPSTONE.md)). The insights agent's output is explicitly framed as *"general wellness information"* with a closing italic disclaimer on every response.

---

## 2. Liability Boundaries

Seven hard rules, enforced consistently across every agent, tool, and prompt:

1. **No supplement dosage recommendations** — food-based suggestions only. `search_nutrient_foods` returns whole foods from CIQUAL; the insights prompt forbids the agent from recommending pills, powders, or specific milligram targets.
2. **No medical claims** — agents never say "this will lower your cholesterol" or "prevents X". Framing is lifestyle optimisation, not clinical benefit.
3. **No calorie auto-adjustment.** TDEE is computed once via the Mifflin-St Jeor equation in [core/tdee.py](../core/tdee.py) with a 1,200 kcal physiological floor. The user **confirms or overrides** the suggestion. `calorie_source` ("calculated" | "manual") is persisted to SQLite so the system always knows whether the target was our suggestion or the user's explicit choice.
4. **No weight trend commentary.** The check-in agent collects an optional weight value for record-keeping only. [prompts/system_prompts.py](../prompts/system_prompts.py) explicitly instructs it never to comment on rate of loss/gain, never to say "you're losing too slowly", and — if the user mentions weight — to respond with *"You can update your calorie target in profile settings if you'd like to adjust."*
5. **No condition-specific advice.** The system does not tailor plans for diabetes, PCOS, IBS, renal disease, or any other diagnosed condition. These need clinical supervision, not an LLM.
6. **Allergen checks are informational.** The `check_allergens` tool flags ingredient matches but every response reminds users that this *"does not replace reading ingredient labels"* — see Section 5.
7. **Every agent response includes a general wellness disclaimer** — a prompt-level requirement, tested in `test_insights.py` and `test_checkin.py`.

Two architectural details reinforce these rules. The insights agent's references are **calorie-proportional and sex-specific** (Capstone 24 in the changelog), computed from the user's own target rather than using a fixed population average — avoiding a systematic over-estimation of "gaps" for users on smaller targets. The nutrient-search tool applies an **impractical-food exclusion filter** (Capstone 14) so the agent doesn't recommend cod liver oil, corn bran, or tea leaf as a "swap".

---

## 3. Data Privacy

All user data — profiles and check-ins — is stored locally in a single SQLite file at [data/user_profiles.db](../data/user_profiles.db). There is no cloud database, no analytics service, no tracking pixel, no telemetry beyond what Streamlit and LangSmith generate themselves. The only third-party data flow is the prompt and response content sent to the **OpenAI API** (and optionally **LangSmith** for observability, when the user sets the tracing environment variables). Both are subject to their respective providers' data usage policies, which the user accepts implicitly by configuring those API keys.

Because the deployed Streamlit Cloud instance uses a hardcoded `user_id = "default_user"`, the demo does not store personally identifying information by default — names are user-provided strings with no verification. Multi-user authentication (Supabase) is explicitly deferred to the commercial track; at that point, a formal data processing agreement, encrypted at-rest storage, and a documented deletion flow become prerequisites before the feature ships. Until then, the system is designed so that wiping [data/user_profiles.db](../data/user_profiles.db) locally is a complete data delete — no distributed copies to chase down.

---

## 4. Bias Considerations

The CIQUAL 2025 food composition database is published by the French food safety agency (Anses). It is authoritative for ingredients common in French and broader European cuisines, but **underrepresents non-European cuisines**. Foods central to Latin American, South Asian, East Asian, West African, and Middle Eastern cooking (black beans, chapati, miso, fufu, ful medames) are less likely to match cleanly by name. This is a known bias, documented in [README.md](../README.md) Known Limitations and acknowledged to the user implicitly through the app's output.

Two implementation choices soften — but do not eliminate — this bias. `lookup_nutrition` in [tools/nutrition_lookup.py](../tools/nutrition_lookup.py) uses pandas substring matching with a `difflib` fuzzy-match fallback, so minor spelling variants still resolve. When a food still cannot be found, the tool returns a graceful "not found" response rather than crashing, and the meal planner agent is prompted to retry with an alternative ingredient name rather than silently substituting a different food. The long-term mitigation — integrating **Open Food Facts** for global branded and regional ingredients — is on the commercial roadmap.

---

## 5. Allergen Safety

The `check_allergens` tool is **informational, not a safety guarantee**. It maps user-declared allergens to the 14 EU-mandated allergens and checks meal ingredient strings for direct matches. It does not parse branded-product ingredient labels, it does not detect cross-contamination risk, and it does not cover regional allergen variants. Agents are prompted to surface allergen flags prominently (the `⚠️ Warnings` block rendered as yellow `st.warning()` alert boxes, per Sprint 3 Fix 3), but every flag is accompanied by the reminder that users must still read the ingredient labels on any packaged product they actually purchase.

For users with a diagnosed severe allergy (e.g. anaphylaxis-grade peanut allergy), NutriMind is a planning aid, not a safety layer. The liability framing is deliberate: the system helps users *find candidate meals* that appear compatible with their declared restrictions, but the final safety check — reading the label on the tin of chickpeas they bought at the supermarket — belongs to the user.

---

## 6. Vulnerable Users

NutriMind is **not designed for** users with active or historical eating disorders, users with pregnancy- or lactation-specific nutritional needs, or users managing diagnosed clinical conditions (diabetes, renal disease, PCOS, coeliac beyond basic gluten-free, etc.). These populations need individualised professional guidance that a general-purpose LLM planner cannot responsibly provide.

Two explicit design decisions reduce harm to these groups even when they do use the app. First, the 1,200 kcal floor in [core/tdee.py](../core/tdee.py) refuses to suggest physiologically unsafe targets regardless of how extreme the user's inputs are. Second, the check-in agent is explicitly prompted to never comment on weight trends, never encourage rapid weight loss, and never offer a calorie recalculation in response to reported weight. The decoupling of check-ins from calorie adjustment — which could easily become an obsessive feedback loop — is the single most important ethical design choice in the system (see Section 6 of [CAPSTONE_PLAN.md](../CAPSTONE_PLAN.md) §2.4 and the "Why qualitative check-ins" rationale in [README.md](../README.md#6-technical-decisions)).

---

## 7. AI Transparency

Users are aware they are interacting with an AI system. The app title in [app.py](../app.py) reads *"NutriMind AI Wellness Coach"*, the subtitle names the three agent capabilities, and every visible response is produced by an LLM. The supervisor's `clarify` fallback surfaces the agent routing explicitly: *"I can help with meal planning, nutritional insights, or a weekly check-in. What would you like to do?"* — making the multi-agent structure legible rather than hidden behind a generic chat persona.

Known LLM limitations are acknowledged and mitigated. The primary risk — **hallucination of nutritional numbers** — is addressed by grounding every macro and micronutrient value in a CIQUAL tool lookup. The insights agent's system prompt explicitly bans invented numbers ("all nutrient values must come from tool calls"), and the regex-based `format_insights` parser ensures the structured state matches the markdown the user actually sees. The meal planner's **calorie validation feedback loop** (Sprint 3 Fix 7) programmatically verifies calorie targets after each plan and routes the agent back with a correction when it drifts outside ±10%. LangSmith tracing is enabled in the deployed instance, so every tool call, agent decision, and LLM response is observable — if a future user reports that the system misbehaved, the trace exists to audit exactly what happened.

---

*Document maintained alongside the code. Last updated: April 2026.*
