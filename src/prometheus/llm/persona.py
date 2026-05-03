"""Persona-voiced message generation with hardcoded fallbacks."""

from __future__ import annotations

from prometheus.llm.gemini import gemini

PERSONA_SYSTEM = """\
You are Prometheus — a personal chief-of-staff for Yash. You write to him directly.

Voice rules (non-negotiable):
- Address him as "Yash" by name occasionally, not every line.
- Short sentences. Direct. Slightly dry. EA-tone.
- No emoji. No exclamation marks. No "Hey", no "Hi there".
- Status-oriented, factual, no flattery.
- Never be chirpy, never use phrases like "great job", "amazing", "excited", "Don't forget!".

Examples to match:
- First fire: "Yash — LinkedIn perf check. You wanted eyes on this by 9:30. Status?"
- First nag: "Still pending. LinkedIn perf check. Quick tap?"
- Third nag: "Yash. LinkedIn check. Three pings in. Want to kill it or do it?"
- Recap: "Day's over. Done: 4. Cancelled: 1. Pending: 2. Missed: 1. Going to sleep?"
- Query response: "Not yet. Scheduled for 9:30, you snoozed once. Currently pending."

Bad (do NOT do): "Hi Yash! 🌟 Don't forget to check LinkedIn perf!"
Good: "Yash — LinkedIn perf check. You wanted eyes on this by 9:30. Status?"
"""


# ---------- Hardcoded fallbacks ----------


def hardcoded_fire_text(title: str, fire_time_ist: str | None) -> str:
    when = f" by {fire_time_ist}" if fire_time_ist else ""
    return f"Yash — {title}.{f' Time was set{when}.' if when else ''} Status?"


def hardcoded_nag_edit(title: str, nag_num: int) -> str:
    if nag_num == 1:
        return f"Still pending. {title}. Quick tap?"
    return f"Second ping. {title}. Where are we?"


def hardcoded_nag_escalated(title: str) -> str:
    return f"Yash. {title}. Three pings in. Kill it or do it?"


def hardcoded_recap(done: int, cancelled: int, pending: int, missed: int) -> str:
    return (
        f"Day's over. Done: {done}. Cancelled: {cancelled}. "
        f"Pending: {pending}. Missed: {missed}. Going to sleep?"
    )


def hardcoded_morning_brief(items: list[tuple[int, str, str]]) -> str:
    """items: list of (reminder_id, title, outcome_label)"""
    if not items:
        return "Morning. Nothing left over from yesterday. Clean slate."
    lines = ["Yash — leftovers from yesterday:"]
    for _rid, title, label in items:
        lines.append(f"• {title} — {label}")
    lines.append("Tap each: today / kill / reschedule.")
    return "\n".join(lines)


def hardcoded_sleep_reprompt() -> str:
    return "Still up? Tap one."


def hardcoded_intent_other() -> str:
    return "Not sure what you're asking. A reminder, or a question about your day?"


# ---------- Gemini-augmented (used Phase 4 for fire/recap copy) ----------


async def generate_fire_text(
    *, title: str, fire_time_ist: str | None, recurrence: str | None
) -> str | None:
    sched = (
        f"recurring {recurrence} at {fire_time_ist} IST"
        if recurrence
        else "one-off"
    )
    prompt = (
        f"Reminder firing now.\n"
        f"Title: {title}\n"
        f"Schedule: {sched}\n\n"
        "Write the FIRST fire message. One short paragraph, max 2 sentences. "
        "Address Yash by name in the first 5 words. End with a status prompt."
    )
    return await gemini().generate_text(
        prompt=prompt,
        system=PERSONA_SYSTEM,
        temperature=0.5,
    )


async def generate_recap_text(
    *, done: int, cancelled: int, pending: int, missed: int
) -> str | None:
    prompt = (
        f"End-of-day recap.\n"
        f"Done: {done}\n"
        f"Cancelled: {cancelled}\n"
        f"Pending: {pending}\n"
        f"Missed: {missed}\n\n"
        "Write the recap. State the numbers exactly. End by asking if he's going to sleep."
    )
    return await gemini().generate_text(
        prompt=prompt, system=PERSONA_SYSTEM, temperature=0.4
    )


async def generate_query_response(*, question: str, memory_blob: str) -> str | None:
    prompt = (
        f"Working memory:\n{memory_blob}\n\n"
        f"Question from Yash: {question}\n\n"
        "Answer factually using the working memory. Short. No filler."
    )
    return await gemini().generate_text(
        prompt=prompt, system=PERSONA_SYSTEM, temperature=0.3
    )
