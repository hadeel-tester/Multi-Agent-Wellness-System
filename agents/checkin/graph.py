"""LangGraph check-in agent.

Graph flow:
    START -> prepare_context -> collect_feedback -> save_check_in_node -> END

Nodes:
    prepare_context     - loads the last check-in (if any) and formats the
                          user profile into a HumanMessage. No LLM call.
    collect_feedback    - single LLM call with CHECK_IN_AGENT_SYSTEM_PROMPT.
                          On turn 1 (just a "check in" request) the agent asks
                          all five questions; on turn 2 (answers supplied) it
                          emits the **Check-In Summary** block.
    save_check_in_node  - regex-extracts the summary from the agent's response.
                          If the marker is present, persists it to SQLite via
                          save_check_in(); otherwise skips the save silently
                          (the agent is still asking questions).
"""

import os
import re
import sys
import time

# Ensure project root is on sys.path when this file is run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import RateLimitError
from langgraph.graph import StateGraph, START, END

from core.memory import load_recent_check_ins, save_check_in
from core.state import CheckInAgentState
from prompts.system_prompts import CHECK_IN_AGENT_SYSTEM_PROMPT

load_dotenv()

# ---------------------------------------------------------------------------
# LangSmith tracing status
# ---------------------------------------------------------------------------

_TRACING_ENABLED = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
_LANGSMITH_PROJECT = os.getenv("LANGCHAIN_PROJECT", "not set")
if _TRACING_ENABLED:
    print(f"[LangSmith] Check-in agent tracing ENABLED — project: {_LANGSMITH_PROJECT}")
else:
    print("[LangSmith] Check-in agent tracing DISABLED — set LANGCHAIN_TRACING_V2=true to enable")

# ---------------------------------------------------------------------------
# LLM (no tools — this is a pure conversational agent)
# ---------------------------------------------------------------------------

_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

# Marker the LLM emits when it has all answers and is ready to summarise.
_SUMMARY_MARKER = "**Check-In Summary**"

# Summary block: prose between the marker and the closing line.
# The prompt instructs the LLM to place the summary inside quotes; we accept
# curly or straight quotes and fall back to any text between the marker and
# the final "saved" line if quoting is missing.
_SUMMARY_RE = re.compile(
    r"\*\*Check-In Summary\*\*\s*\n+.*?[\"\u201c](.+?)[\"\u201d]",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def _format_profile(profile: dict) -> str:
    """Render the profile fields relevant to check-in tone/context."""
    goals = profile.get("health_goals") or "general wellness"
    restrictions = profile.get("dietary_restrictions") or []
    allergies = profile.get("allergies") or []
    return (
        f"Health goals: {goals} · "
        f"Dietary restrictions: {', '.join(restrictions) if restrictions else 'none'} · "
        f"Allergies: {', '.join(allergies) if allergies else 'none'}"
    )


def prepare_context(state: CheckInAgentState) -> dict:
    """Entry node: inject profile + last check-in summary as a SystemMessage.

    Uses SystemMessage (not HumanMessage) so that the user's own message
    remains the latest HumanMessage in the history — otherwise the LLM
    treats this framing as the "user's latest turn" and asks questions
    back even when the real user message already contains answers.
    """
    user_id = state.get("user_id") or "default_user"
    profile = state.get("user_profile") or {}
    profile_text = _format_profile(profile)

    recent = load_recent_check_ins(user_id, limit=1)
    if recent and recent[0].get("notes"):
        last_summary = recent[0]["notes"]
        history_line = f"Last time you mentioned: \"{last_summary}\""
    else:
        history_line = "This is your first check-in."

    context = (
        f"[User profile]\n{profile_text}\n\n"
        f"[Previous check-in]\n{history_line}\n\n"
        f"Inspect the user's latest HumanMessage. If it contains structured "
        f"answers (Adherence:, Problem meals:, Energy level:, Weight:, "
        f"Additional notes:, or similar), skip questions and emit the "
        f"**Check-In Summary** block directly. Otherwise ask all five "
        f"questions in one message."
    )
    return {"messages": [SystemMessage(content=context)]}


def collect_feedback(state: CheckInAgentState) -> dict:
    """LLM call: ask the questions (turn 1) or emit the summary (turn 2)."""
    system_msg = SystemMessage(content=CHECK_IN_AGENT_SYSTEM_PROMPT)
    messages = [system_msg] + list(state["messages"])
    for attempt in range(3):
        try:
            response = _llm.invoke(messages)
            return {"messages": [response]}
        except RateLimitError:
            if attempt < 2:
                time.sleep(2)
                continue
            return {
                "messages": [AIMessage(
                    content="The check-in service is briefly unavailable. Please try again in a moment."
                )],
                "error": "Rate limit exceeded after 3 attempts.",
            }
    raise RuntimeError("collect_feedback() exited retry loop without returning")


def _extract_summary(raw: str) -> str | None:
    """Return the summary text if the marker is present, else None.

    Primary parse: text between the first pair of quotes after the marker.
    Fallback: any non-empty text that follows the marker paragraph.
    """
    if _SUMMARY_MARKER not in raw:
        return None

    m = _SUMMARY_RE.search(raw)
    if m:
        return m.group(1).strip()

    # Graceful degradation — marker present but quoted block missing.
    # Take everything after the marker, strip leading blank lines, and
    # treat the first non-empty paragraph as the summary.
    after = raw.split(_SUMMARY_MARKER, 1)[1].strip()
    if not after:
        return None
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", after) if p.strip()]
    for para in paragraphs:
        cleaned = para.strip().strip("\"\u201c\u201d")
        # Skip the "Here's what I'll pass along…" lead-in if present.
        if cleaned.lower().startswith("here's what"):
            continue
        if cleaned:
            return cleaned
    return None


def save_check_in_node(state: CheckInAgentState) -> dict:
    """Persist the check-in if the agent's last message contains a summary.

    If the **Check-In Summary** marker is absent, the agent is still asking
    questions — skip the save and leave summary/check_in_data empty.
    """
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    raw = ""
    if last is not None:
        content = getattr(last, "content", "")
        raw = content if isinstance(content, str) else str(content)

    summary = _extract_summary(raw) if raw else None

    if not summary:
        return {"summary": "", "check_in_data": {}}

    check_in_data = {"notes": summary}
    user_id = state.get("user_id") or "default_user"
    try:
        save_check_in(user_id, check_in_data)
    except Exception as exc:  # pragma: no cover — DB errors shouldn't crash the graph
        return {
            "summary": summary,
            "check_in_data": check_in_data,
            "error": f"Failed to persist check-in: {exc}",
        }

    return {"summary": summary, "check_in_data": check_in_data}


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

_builder = StateGraph(CheckInAgentState)

_builder.add_node("prepare_context", prepare_context)
_builder.add_node("collect_feedback", collect_feedback)
_builder.add_node("save_check_in_node", save_check_in_node)

_builder.add_edge(START, "prepare_context")
_builder.add_edge("prepare_context", "collect_feedback")
_builder.add_edge("collect_feedback", "save_check_in_node")
_builder.add_edge("save_check_in_node", END)

checkin_agent = _builder.compile()

__all__ = ["checkin_agent"]


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running check-in agent smoke-test...\n")

    initial_state = {
        "messages": [HumanMessage(content="I'd like to do my weekly check-in.")],
        "user_profile": {
            "health_goals": "balanced nutrition",
            "dietary_restrictions": [],
            "allergies": [],
        },
        "user_id": "smoke_test_user",
        "check_in_data": {},
        "summary": "",
        "error": None,
    }

    result = checkin_agent.invoke(
        initial_state,
        config={
            "recursion_limit": 10,
            "run_name": "checkin_smoke_test",
            "metadata": {"sprint": "capstone", "agent": "checkin"},
        },
    )

    last = result["messages"][-1]
    print("=== Agent response ===")
    print(last.content)
    print("\n=== Summary ===")
    print(result.get("summary") or "(none — agent is still asking questions)")
    if result.get("error"):
        print(f"\nError: {result['error']}")
