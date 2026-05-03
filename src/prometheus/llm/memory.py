"""Working memory builder + summary regeneration + conversational Q&A."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from prometheus.db.repos import (
    fires_in_window,
    last_n_chat_messages,
    latest_memory_summary,
    list_active_reminders,
)
from prometheus.llm.gemini import gemini
from prometheus.utils.time import fmt_ist, now_ist, now_utc


async def build_working_memory(session: AsyncSession, user_id: int) -> dict:
    summary = await latest_memory_summary(session)
    msgs = await last_n_chat_messages(session, user_id, 50)
    reminders = await list_active_reminders(session, user_id)

    seven_days_ago = now_utc() - timedelta(days=7)
    fires = await fires_in_window(session, user_id, seven_days_ago, now_utc())

    return {
        "now_ist": now_ist().strftime("%Y-%m-%dT%H:%M:%S"),
        "rolling_summary": summary.summary_text if summary else "",
        "active_reminders": [
            {
                "id": r.id,
                "title": r.title,
                "type": r.schedule_type.value,
                "rule": r.recurrence_rule,
                "time_ist": r.fire_time.strftime("%H:%M") if r.fire_time else None,
                "one_off_ist": fmt_ist(r.one_off_datetime, with_date=True)
                if r.one_off_datetime
                else None,
            }
            for r in reminders
        ],
        "last_7_days_fires": [
            {
                "fire_id": f.id,
                "reminder_id": f.reminder_id,
                "fired_at_ist": fmt_ist(f.fired_at, with_date=True),
                "outcome": f.outcome.value,
                "snooze_count": f.snooze_count,
                "note": (f.note_text or "")[:200],
            }
            for f in fires
        ],
        "last_chat": [
            {
                "role": m.role.value,
                "content": m.content,
                "at": fmt_ist(m.created_at),
            }
            for m in msgs
        ],
    }


def memory_to_blob(mem: dict) -> str:
    return json.dumps(mem, ensure_ascii=False, indent=2)


# ---------- Summary regen (weekly Sunday 03:00 IST) ----------


_SUMMARY_SYSTEM = """\
You write a rolling personal summary for Yash.

Compress the events below into a short paragraph (4-6 sentences) capturing:
- What recurring rituals he stuck to vs. abandoned
- Notable misses or patterns
- Anything mentioned in notes that signals priorities

Voice: dry, factual, third-person. No advice. No emoji.
"""


async def regenerate_summary(
    *, fires_window: list[dict], chat_window: list[dict], prev_summary: str | None
) -> str | None:
    payload = {
        "previous_summary": prev_summary or "",
        "fires_in_window": fires_window,
        "chat_in_window": chat_window,
    }
    prompt = json.dumps(payload, ensure_ascii=False, indent=2)
    return await gemini().generate_text(
        prompt=prompt, system=_SUMMARY_SYSTEM, temperature=0.3
    )


def fires_for_summary(fires) -> list[dict]:
    return [
        {
            "fired_at_ist": fmt_ist(f.fired_at, with_date=True),
            "outcome": f.outcome.value,
            "snooze_count": f.snooze_count,
            "note": (f.note_text or "")[:300],
            "reminder_id": f.reminder_id,
        }
        for f in fires
    ]


def chats_for_summary(msgs) -> list[dict]:
    return [
        {
            "at": fmt_ist(m.created_at, with_date=True),
            "role": m.role.value,
            "content": m.content[:500],
        }
        for m in msgs
    ]
