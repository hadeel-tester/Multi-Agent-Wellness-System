"""Streamlit UI for the Multi-Agent Wellness System.

Layout:
    Sidebar  — user health profile form (persisted via SQLite)
    Tab 1    — Generate Plan: text input → agent invocation → meal plan display
    Tab 2    — Shopping List: items from the last generated plan

No business logic here. All logic lives in core/ and tools/.
"""

import html
import re

import streamlit as st
from langchain_core.messages import HumanMessage, ToolMessage

from core.supervisor import supervisor_agent, MAX_ITERATIONS
from core.memory import init_db, load_profile, save_profile, load_recent_check_ins
from core.tdee import calculate_tdee

# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="NutriMind AI Wellness Coach",
    page_icon="🥗",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

init_db()  # idempotent — creates tables if they don't exist

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEALTH_GOALS_OPTIONS = [
    "Heart health",
    "Diabetes management",
    "High protein",
    "Balanced nutrition",
    "Low sodium",
    "Mediterranean style",
]

DIETARY_RESTRICTIONS_OPTIONS = [
    "Vegetarian",
    "Vegan",
    "Gluten-free",
    "Dairy-free",
    "Halal",
    "Kosher",
]

ACTIVITY_LABELS = {
    "Sedentary":         "sedentary",
    "Lightly Active":    "light",
    "Moderately Active": "moderate",
    "Active":            "active",
    "Very Active":       "very_active",
}
GOAL_LABELS = {
    "Lose Weight":     "lose",
    "Maintain Weight": "maintain",
    "Gain Weight":     "gain",
}
_ACTIVITY_VALUE_TO_LABEL = {v: k for k, v in ACTIVITY_LABELS.items()}
_GOAL_VALUE_TO_LABEL = {v: k for k, v in GOAL_LABELS.items()}

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "user_id" not in st.session_state:
    st.session_state.user_id = "default_user"
    # CAPSTONE: Replace hardcoded user_id with real authentication

if "profile_loaded" not in st.session_state:
    st.session_state.profile_loaded = False

if "last_meal_plan" not in st.session_state:
    st.session_state.last_meal_plan = {}

if "last_shopping_list" not in st.session_state:
    st.session_state.last_shopping_list = []

if "last_agent_response" not in st.session_state:
    st.session_state.last_agent_response = ""

if "last_allergen_warnings" not in st.session_state:
    st.session_state.last_allergen_warnings = []

if "last_safety_notes" not in st.session_state:
    st.session_state.last_safety_notes = []

if "last_insights_response" not in st.session_state:
    st.session_state.last_insights_response = ""

if "last_checkin_response" not in st.session_state:
    st.session_state.last_checkin_response = ""

if "check_in_history" not in st.session_state:
    st.session_state.check_in_history = []

# Pre-fill sidebar from SQLite on the first run of the session
if not st.session_state.profile_loaded:
    _existing = load_profile(st.session_state.user_id)
    if _existing:
        st.session_state._prefill = _existing
    st.session_state.profile_loaded = True

_prefill: dict = st.session_state.get("_prefill") or {}

# ---------------------------------------------------------------------------
# Sidebar — health profile form
# ---------------------------------------------------------------------------

st.sidebar.title("Your Health Profile")

name = st.sidebar.text_input("Name", value=_prefill.get("name") or "")

# ── Live-updating TDEE inputs (outside form — reruns immediately on change) ──

age = st.sidebar.number_input(
    "Age",
    min_value=1,
    max_value=120,
    step=1,
    value=int(_prefill.get("age") or 25),
)
_sex_options = ["Male", "Female", "Prefer not to say"]
_sex_prefill = _prefill.get("sex") or "Prefer not to say"
sex = st.sidebar.radio(
    "Gender",
    options=_sex_options,
    index=_sex_options.index(_sex_prefill) if _sex_prefill in _sex_options else 2,
    horizontal=True,
)
weight_kg = st.sidebar.number_input(
    "Weight (kg)",
    min_value=20.0,
    max_value=300.0,
    step=0.5,
    value=float(_prefill.get("weight_kg") or 70.0),
)
height_cm = st.sidebar.number_input(
    "Height (cm)",
    min_value=50,
    max_value=250,
    step=1,
    value=int(_prefill.get("height_cm") or 170),
)

_activity_options = list(ACTIVITY_LABELS.keys())
_prefill_activity_internal = _prefill.get("activity_level") or "moderate"
_prefill_activity_label = _ACTIVITY_VALUE_TO_LABEL.get(
    _prefill_activity_internal, "Moderately Active"
)
activity_label = st.sidebar.selectbox(
    "Activity level",
    options=_activity_options,
    index=_activity_options.index(_prefill_activity_label),
)

_goal_options = list(GOAL_LABELS.keys())
_prefill_goal_internal = _prefill.get("goal") or "maintain"
_prefill_goal_label = _GOAL_VALUE_TO_LABEL.get(
    _prefill_goal_internal, "Maintain Weight"
)
goal_label = st.sidebar.selectbox(
    "Goal",
    options=_goal_options,
    index=_goal_options.index(_prefill_goal_label),
)

_tdee = calculate_tdee(
    weight_kg=float(weight_kg),
    height_cm=float(height_cm),
    age=int(age),
    sex=sex,
    activity_level=ACTIVITY_LABELS[activity_label],
    goal=GOAL_LABELS[goal_label],
)
suggested_calories = _tdee["suggested_calories"]
st.sidebar.info(
    f"Suggested daily intake: {suggested_calories} kcal/day "
    f"(based on your profile)"
)

_prefill_source = _prefill.get("calorie_source")
_use_suggested_default = True if _prefill_source is None else (_prefill_source == "calculated")
use_suggested = st.sidebar.checkbox(
    "Use suggested calories",
    value=_use_suggested_default,
)
if use_suggested:
    calorie_target = suggested_calories
else:
    calorie_target = st.sidebar.number_input(
        "Daily calorie target (kcal)",
        min_value=800,
        max_value=5000,
        step=50,
        value=int(_prefill.get("calorie_target") or suggested_calories),
    )

# ── Remaining profile fields + save button ───────────────────────────────────

health_goals = st.sidebar.multiselect(
    "Dietary focus",
    options=HEALTH_GOALS_OPTIONS,
    default=[g for g in (_prefill.get("health_goals") or []) if g in HEALTH_GOALS_OPTIONS],
)
dietary_restrictions = st.sidebar.multiselect(
    "Dietary restrictions",
    options=DIETARY_RESTRICTIONS_OPTIONS,
    default=[r for r in (_prefill.get("dietary_restrictions") or []) if r in DIETARY_RESTRICTIONS_OPTIONS],
)
allergies_raw = st.sidebar.text_input(
    "Allergies (comma-separated)",
    value=", ".join(_prefill.get("allergies") or []),
)
submitted = st.sidebar.button("Save Profile")

if submitted:
    save_profile(
        st.session_state.user_id,
        {
            "name": name,
            "age": age,
            "sex": sex,
            "weight_kg": weight_kg,
            "height_cm": height_cm,
            "calorie_target": calorie_target,
            "activity_level": ACTIVITY_LABELS[activity_label],
            "goal": GOAL_LABELS[goal_label],
            "calorie_source": "calculated" if use_suggested else "manual",
            "health_goals": health_goals,
            "dietary_restrictions": dietary_restrictions,
            "allergies": [a.strip() for a in allergies_raw.split(",") if a.strip()],
        },
    )
    # Refresh prefill so the form reflects saved values on next rerun
    st.session_state._prefill = load_profile(st.session_state.user_id) or {}
    st.sidebar.success("Profile saved!")

st.sidebar.caption(
    "NutriMind provides general wellness information. "
    "Not a substitute for professional dietary advice."
)

# ---------------------------------------------------------------------------
# Helper: extract warnings from tool messages
# ---------------------------------------------------------------------------


def _collect_tool_signals(messages: list) -> tuple[list[str], list[str]]:
    """Walk the agent's message history and pull out allergen and safety signals.

    Returns:
        (allergen_warnings, safety_notes) — each is a deduplicated list of
        human-readable strings sourced from the check_allergens and
        validate_meal_safety tool calls.
    """
    allergen_warnings: list[str] = []
    safety_notes: list[str] = []
    seen_allergen: set[str] = set()
    seen_safety: set[str] = set()

    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        name = getattr(msg, "name", "") or ""
        content = getattr(msg, "content", "")
        text = content if isinstance(content, str) else str(content)

        if name == "check_allergens":
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("WARNING") and line not in seen_allergen:
                    seen_allergen.add(line)
                    allergen_warnings.append(line)
        elif name == "validate_meal_safety":
            # Pull out any retrieved entries flagged as high risk.
            for line in text.splitlines():
                stripped = line.strip()
                if "risk: high" in stripped.lower() and stripped not in seen_safety:
                    seen_safety.add(stripped)
                    safety_notes.append(stripped)

    return allergen_warnings, safety_notes


# ---------------------------------------------------------------------------
# Helper: split agent response into per-day chunks
# ---------------------------------------------------------------------------

# Matches lines that open a day section: "Day 1", "## Day 2", "**Day 3**", etc.
# Does NOT match "**Day 1 Total**" (Total prevents the end-anchor from firing).
_DAY_HEADING_RE = re.compile(
    r'^(?:#{1,3}\s+|\*{2})?Day\s+(\d+)(?:\*{2})?\s*$',
    re.IGNORECASE,
)

# Matches the end-of-day summary header exactly: **Day N Total**
_DAY_TOTAL_RE = re.compile(r'^\*\*Day\s+\d+\s+Total\*\*\s*$', re.IGNORECASE)


def _split_into_days(text: str) -> tuple[str, list[tuple[int, str]], str]:
    """Split the agent's response into preamble, per-day chunks, and trailing text.

    Returns:
        preamble  — text before the first day heading (may be empty)
        day_chunks — list of (day_number, chunk_text) in order
        trailing  — text after the last Day N Total block (may be empty)

    Falls back gracefully: if no day headings are found, returns
    (text, [], "") so the caller can render the whole response as-is.
    """
    lines = text.splitlines()

    # Locate lines that open a new day section
    day_starts: list[tuple[int, int]] = []  # (line_index, day_number)
    for i, line in enumerate(lines):
        m = _DAY_HEADING_RE.match(line.strip())
        if m:
            day_starts.append((i, int(m.group(1))))

    if not day_starts:
        return text, [], ""

    preamble = "\n".join(lines[: day_starts[0][0]]).strip()

    # Build raw day chunks (slice by heading positions)
    day_chunks: list[tuple[int, str]] = []
    for idx, (start_line, day_num) in enumerate(day_starts):
        end_line = day_starts[idx + 1][0] if idx + 1 < len(day_starts) else len(lines)
        chunk = "\n".join(lines[start_line:end_line]).strip()
        day_chunks.append((day_num, chunk))

    # Separate trailing text from the last day chunk.
    # Trailing content begins after the last "**Day N Total**" section
    # (bold header line + blank line + kcal totals line + optional blank).
    trailing = ""
    if day_chunks:
        last_day_num, last_chunk = day_chunks[-1]
        last_chunk_lines = last_chunk.splitlines()

        last_total_idx = -1
        for i, line in enumerate(last_chunk_lines):
            if _DAY_TOTAL_RE.match(line.strip()):
                last_total_idx = i

        if last_total_idx >= 0:
            cutoff = last_total_idx + 1
            # Skip blank lines between the header and the kcal line
            while cutoff < len(last_chunk_lines) and not last_chunk_lines[cutoff].strip():
                cutoff += 1
            # Consume the kcal totals line itself
            if cutoff < len(last_chunk_lines):
                cutoff += 1
            # Consume any blank lines that close this section
            while cutoff < len(last_chunk_lines) and not last_chunk_lines[cutoff].strip():
                cutoff += 1

            trailing = "\n".join(last_chunk_lines[cutoff:]).strip()
            day_chunks[-1] = (last_day_num, "\n".join(last_chunk_lines[:cutoff]).strip())

    return preamble, day_chunks, trailing


# ---------------------------------------------------------------------------
# Helper: render meal plan markdown with inline ⚠️ Warnings blocks
# ---------------------------------------------------------------------------


def _render_plan_markdown(response_text: str) -> None:
    """Render meal plan text, converting ⚠️ Warnings blocks to st.warning() calls.

    Lines starting with ⚠️ trigger warning mode. Subsequent bullet lines are
    collected and rendered as a yellow st.warning() alert box. Everything else
    is passed through as normal markdown.
    """
    markdown_buffer: list[str] = []
    warning_buffer: list[str] = []
    in_warnings = False

    def flush_markdown() -> None:
        if markdown_buffer:
            st.markdown("\n".join(markdown_buffer))
            markdown_buffer.clear()

    def flush_warning() -> None:
        if warning_buffer:
            st.warning("\n".join(warning_buffer))
            warning_buffer.clear()

    for line in response_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("⚠️") or stripped.lower().startswith("warnings"):
            flush_markdown()
            in_warnings = True
            warning_buffer.append(stripped)
        elif in_warnings:
            if stripped.startswith("- ") or stripped.startswith("* "):
                warning_buffer.append(stripped)
            elif stripped == "":
                pass  # blank lines inside the warnings block — skip
            else:
                flush_warning()
                in_warnings = False
                markdown_buffer.append(line)
        else:
            markdown_buffer.append(line)

    flush_warning()
    flush_markdown()


# ---------------------------------------------------------------------------
# Helper: render full agent response
# ---------------------------------------------------------------------------


def _render_agent_response(
    response_text: str,
    allergen_warnings: list[str],
    safety_notes: list[str],
) -> None:
    """Render the agent's full meal plan response with prominent safety alerts.

    Allergen warnings are shown first (red st.error), then any high-risk safety
    notes from the RAG validator (amber st.warning), then the full agent
    markdown underneath so the reader never misses a dietary alert.
    """
    if not response_text and not allergen_warnings and not safety_notes:
        st.info("No meal plan yet. Enter a request above and click Generate Plan.")
        return

    if allergen_warnings:
        st.error("Allergen alerts detected — review before cooking:")
        for warn in allergen_warnings:
            st.warning(warn)

    if safety_notes:
        st.warning("Safety notes from the ingredient knowledge base:")
        for note in safety_notes:
            st.info(note)

    if response_text:
        preamble, day_chunks, trailing = _split_into_days(response_text)

        if preamble:
            _render_plan_markdown(preamble)

        if day_chunks:
            for day_num, chunk in day_chunks:
                with st.expander(f"Day {day_num}", expanded=True):
                    _render_plan_markdown(chunk)
        else:
            # No day headings found — fall back to flat rendering
            _render_plan_markdown(response_text)

        if trailing:
            _render_plan_markdown(trailing)


# ---------------------------------------------------------------------------
# Helper: wrap markdown-ish text in a printable HTML document
# ---------------------------------------------------------------------------

_HTML_STYLE = """
body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
       max-width: 800px; margin: auto; padding: 20px; line-height: 1.6;
       color: #222; }
h1 { border-bottom: 2px solid #333; padding-bottom: 8px; }
h2 { margin-top: 1.4em; }
h3 { margin-top: 1.2em; }
ul, ol { padding-left: 1.4em; }
li { margin: 0.2em 0; }
label { display: block; margin: 0.25em 0; }
@media print { input[type=checkbox] { appearance: auto; } }
"""


def _md_body_to_html(body_md: str) -> str:
    """Convert a small subset of markdown to HTML using string formatting.

    Supports: #/##/### headings, **bold**, *italic*, - bullets, 1. numbered
    lists, and blank-line-separated paragraphs. Unknown syntax is passed
    through as escaped text.
    """
    try:
        import markdown  # type: ignore
        return markdown.markdown(body_md, extensions=["extra", "sane_lists"])
    except Exception:
        pass

    lines = body_md.splitlines()
    out: list[str] = []
    list_type: str | None = None  # "ul" | "ol" | None
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(paragraph).strip()
            if text:
                out.append(f"<p>{_inline(text)}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            out.append(f"</{list_type}>")
            list_type = None

    def open_list(kind: str) -> None:
        nonlocal list_type
        if list_type != kind:
            close_list()
            out.append(f"<{kind}>")
            list_type = kind

    def _inline(text: str) -> str:
        escaped = html.escape(text)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
        return escaped

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            close_list()
            continue

        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        if bullet:
            flush_paragraph()
            open_list("ul")
            out.append(f"<li>{_inline(bullet.group(1))}</li>")
            continue

        numbered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if numbered:
            flush_paragraph()
            open_list("ol")
            out.append(f"<li>{_inline(numbered.group(1))}</li>")
            continue

        close_list()
        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    return "\n".join(out)


def _wrap_html(title: str, body_md: str) -> str:
    """Wrap markdown-ish body text in a self-contained printable HTML document."""
    safe_title = html.escape(title)
    body_html = _md_body_to_html(body_md)
    return (
        "<!DOCTYPE html>\n"
        f"<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{safe_title}</title>\n"
        f"<style>{_HTML_STYLE}</style>\n"
        "</head>\n<body>\n"
        f"<h1>{safe_title}</h1>\n"
        f"{body_html}\n"
        "</body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# Main area — two tabs
# ---------------------------------------------------------------------------

st.title("NutriMind AI Wellness Coach")
st.caption("AI-powered meal planning, nutritional insights, and wellness tracking")

tab_plan, tab_shopping, tab_insights, tab_checkin = st.tabs(["Generate Plan", "Shopping List", "Nutritional Insights", "Weekly Check-In"])

# ── Tab 1: Generate Plan ────────────────────────────────────────────────────

with tab_plan:
    user_request = st.text_input(
        "What would you like?",
        placeholder="e.g. Plan 3 days of healthy meals for weight loss",
    )

    if st.button("Generate Plan", type="primary"):
        if not user_request.strip():
            st.warning("Please enter a meal planning request.")
        else:
            with st.spinner("Generating your personalised meal plan..."):
                try:
                    config = {
                        "recursion_limit": MAX_ITERATIONS,
                        "run_name": "supervisor_meal_plan",
                        "metadata": {
                            "user_id": st.session_state.user_id,
                            "sprint": "capstone",
                        },
                    }
                    initial_state = {
                        "messages": [HumanMessage(content=user_request)],
                        "user_id": st.session_state.user_id,
                        "user_profile": {},
                        "meal_plan": {},
                        "shopping_list": [],
                        "current_step": "start",
                        "error": None,
                        "route_to": "",
                        "insights": {},
                        "check_in_history": st.session_state.check_in_history,
                    }
                    result = supervisor_agent.invoke(initial_state, config=config)

                    st.session_state.last_meal_plan = result.get("meal_plan", {})
                    st.session_state.last_shopping_list = result.get("shopping_list", [])

                    # Capture the agent's full natural-language response so the
                    # UI can show preparation hints, health scores, and the
                    # per-meal macro breakdown — not just the stripped JSON.
                    messages = result.get("messages", [])
                    last_msg = messages[-1] if messages else None
                    response_content = getattr(last_msg, "content", "") if last_msg else ""
                    st.session_state.last_agent_response = (
                        response_content
                        if isinstance(response_content, str)
                        else str(response_content)
                    )

                    allergen_warnings, safety_notes = _collect_tool_signals(messages)
                    st.session_state.last_allergen_warnings = allergen_warnings
                    st.session_state.last_safety_notes = safety_notes

                    if result.get("error"):
                        st.error(f"Agent error: {result['error']}")

                    # Token usage — present on last AIMessage when available
                    usage = getattr(last_msg, "usage_metadata", None) if last_msg else None
                    if usage:
                        st.caption(
                            f"Tokens used — input: {usage.get('input_tokens', '?')}  "
                            f"output: {usage.get('output_tokens', '?')}"
                        )

                except Exception as exc:
                    st.error(f"Failed to generate plan: {exc}")

    _render_agent_response(
        st.session_state.last_agent_response,
        st.session_state.last_allergen_warnings,
        st.session_state.last_safety_notes,
    )

    if st.session_state.last_agent_response:
        st.download_button(
            label="Download Meal Plan",
            data=_wrap_html("NutriMind Meal Plan", st.session_state.last_agent_response),
            file_name="nutrimind_meal_plan.html",
            mime="text/html",
        )

# ── Tab 2: Shopping List ────────────────────────────────────────────────────

with tab_shopping:
    shopping = st.session_state.last_shopping_list
    if not shopping:
        st.info("Generate a meal plan first to see the shopping list.")
    else:
        st.markdown(f"**{len(shopping)} items**")
        for item in shopping:
            st.markdown(f"- {item}")

        shopping_items_html = "\n".join(
            f'<label><input type="checkbox"> {html.escape(item)}</label><br>'
            for item in shopping
        )
        shopping_html = (
            "<!DOCTYPE html>\n"
            "<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<title>NutriMind Shopping List</title>\n"
            f"<style>{_HTML_STYLE}</style>\n"
            "</head>\n<body>\n"
            "<h1>NutriMind Shopping List</h1>\n"
            f"{shopping_items_html}\n"
            "</body>\n</html>\n"
        )
        st.download_button(
            label="Download Shopping List",
            data=shopping_html,
            file_name="nutrimind_shopping_list.html",
            mime="text/html",
        )

# ── Tab 3: Nutritional Insights ─────────────────────────────────────────────

with tab_insights:
    if st.button("Analyse My Plan", type="primary"):
        if not st.session_state.last_meal_plan:
            st.warning("Generate a meal plan first.")
        else:
            with st.spinner("Analysing nutritional gaps..."):
                try:
                    config = {
                        "recursion_limit": 40,
                        "run_name": "supervisor_insights",
                        "metadata": {
                            "user_id": st.session_state.user_id,
                            "sprint": "capstone",
                        },
                    }
                    initial_state = {
                        "messages": [HumanMessage(content="Analyse my meal plan for nutritional gaps")],
                        "user_id": st.session_state.user_id,
                        "user_profile": _prefill,
                        "meal_plan": st.session_state.last_meal_plan,
                        "shopping_list": [],
                        "current_step": "start",
                        "error": None,
                        "route_to": "",
                        "insights": {},
                        "check_in_history": [],
                    }
                    result = supervisor_agent.invoke(initial_state, config=config)

                    messages = result.get("messages", [])
                    last_msg = messages[-1] if messages else None
                    response_content = getattr(last_msg, "content", "") if last_msg else ""
                    st.session_state.last_insights_response = (
                        response_content
                        if isinstance(response_content, str)
                        else str(response_content)
                    )

                    if result.get("error"):
                        st.error(f"Agent error: {result['error']}")

                except Exception as exc:
                    st.error(f"Failed to analyse plan: {exc}")

    if st.session_state.last_insights_response:
        st.markdown(st.session_state.last_insights_response)
        st.download_button(
            label="Download Nutritional Analysis",
            data=_wrap_html(
                "NutriMind Nutritional Analysis",
                st.session_state.last_insights_response,
            ),
            file_name="nutrimind_nutritional_analysis.html",
            mime="text/html",
        )
    else:
        st.info("Click 'Analyse My Plan' to see nutritional gap analysis for your current meal plan.")

# ── Tab 4: Weekly Check-In ──────────────────────────────────────────────────

with tab_checkin:
    st.markdown(
        "Share how your week went and we'll use your feedback to improve your next meal plan."
    )
    st.markdown("---")

    adherence = st.radio(
        "How closely did you follow the meal plan?",
        options=["Followed fully", "Mostly followed", "Partially followed", "Did not follow"],
        horizontal=True,
    )
    problem_meals = st.text_area(
        "Any meals that didn't work? What went wrong?",
        placeholder="e.g. The lentil soup was too heavy for lunch.",
    )
    energy = st.radio(
        "How was your energy this week?",
        options=["Great", "Okay", "Low", "Very low"],
        horizontal=True,
    )
    share_weight = st.checkbox("Share current weight (optional)")
    checkin_weight = None
    if share_weight:
        checkin_weight = st.number_input(
            "Current weight (kg)",
            min_value=20.0,
            max_value=300.0,
            step=0.5,
            value=float(_prefill.get("weight_kg") or 70.0),
        )
    notes = st.text_area(
        "Anything else the meal planner should know?",
        placeholder="e.g. Prefer quicker dinners, no more than 30 minutes.",
    )

    if st.button("Submit Check-In", type="primary"):
        weight_part = f" Weight: {checkin_weight} kg." if checkin_weight is not None else ""
        checkin_message = (
            f"Weekly check-in: "
            f"Adherence: {adherence.lower()}. "
            f"Problem meals: {problem_meals.strip() or 'none'}. "
            f"Energy: {energy.lower()}."
            f"{weight_part} "
            f"Notes: {notes.strip() or 'none'}."
        )

        with st.spinner("Saving your check-in..."):
            try:
                config = {
                    "recursion_limit": 10,
                    "run_name": "supervisor_check_in",
                    "metadata": {
                        "user_id": st.session_state.user_id,
                        "sprint": "capstone",
                    },
                }
                initial_state = {
                    "messages": [HumanMessage(content=checkin_message)],
                    "user_id": st.session_state.user_id,
                    "user_profile": _prefill,
                    "meal_plan": {},
                    "shopping_list": [],
                    "current_step": "start",
                    "error": None,
                    "route_to": "",
                    "insights": {},
                    "check_in_history": [],
                    "calorie_retries": 0,
                }
                result = supervisor_agent.invoke(initial_state, config=config)

                messages = result.get("messages", [])
                last_msg = messages[-1] if messages else None
                response_content = getattr(last_msg, "content", "") if last_msg else ""
                st.session_state.last_checkin_response = (
                    response_content
                    if isinstance(response_content, str)
                    else str(response_content)
                )
                st.session_state.check_in_history = result.get("check_in_history", [])

                if result.get("error"):
                    st.error(f"Agent error: {result['error']}")
                else:
                    st.success("Check-in saved! Your feedback will be used in your next meal plan.")

            except Exception as exc:
                st.error(f"Failed to save check-in: {exc}")

    if st.session_state.last_checkin_response:
        st.markdown(st.session_state.last_checkin_response)

    # ── Check-in history ────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Previous Check-Ins")
    history = load_recent_check_ins(st.session_state.user_id, limit=2)
    if not history:
        st.info("No check-ins recorded yet.")
    else:
        for row in history:
            raw_date = row.get("created_at", "")
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(raw_date)
                date_str = dt.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
            except Exception:
                date_str = raw_date
            with st.container(border=True):
                st.caption(date_str)
                st.markdown(row.get("notes") or "_No summary recorded._")

# CAPSTONE: Add progress tracking charts (weight, calorie adherence over weeks)
# CAPSTONE: Add supplement recommendations tab
# CAPSTONE: Add multi-user login / user switcher
