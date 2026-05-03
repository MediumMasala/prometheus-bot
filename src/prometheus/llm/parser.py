"""Parse free-text reminder requests into structured JSON.

Primary path: Gemini structured output.
Fallback: regex for common patterns ("at HH:MM", "in N min", "every weekday at HH:MM").
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from prometheus.llm.gemini import gemini
from prometheus.utils.logging import log
from prometheus.utils.time import IST, now_ist, to_utc


class ParsedReminder(BaseModel):
    title: str = Field(description="Short imperative title for the reminder")
    schedule_type: Literal["one_off", "recurring"]
    recurrence_rule: str | None = Field(
        default=None,
        description="One of: 'daily', 'weekdays', 'weekend', 'weekly:mon,wed,fri'. None for one_off.",
    )
    fire_time_ist: str | None = Field(
        default=None,
        description="HH:MM 24h IST. Required for recurring.",
    )
    one_off_iso_ist: str | None = Field(
        default=None,
        description="ISO datetime in IST 'YYYY-MM-DDTHH:MM:SS' for one_off. None for recurring.",
    )
    confidence: float = Field(ge=0.0, le=1.0)


class IntentClassification(BaseModel):
    intent: Literal["create_reminder", "query", "other"]
    confidence: float = Field(ge=0.0, le=1.0)


_PARSER_SYSTEM = """\
You parse reminder requests for a single user (Yash) into structured JSON.

Times are India Standard Time (IST, UTC+5:30) unless explicitly stated otherwise.
"now" = the current IST time the user is sending. Resolve relative phrases ("in 30 min", "tomorrow", "tonight") against the provided current_ist.

Output schema:
- title: short imperative, e.g. "Check LinkedIn perf"
- schedule_type: "recurring" if it repeats, else "one_off"
- recurrence_rule (recurring only): "daily" | "weekdays" | "weekend" | "weekly:<comma-list of mon,tue,wed,thu,fri,sat,sun>"
- fire_time_ist (recurring only): HH:MM 24h
- one_off_iso_ist (one_off only): YYYY-MM-DDTHH:MM:SS in IST
- confidence: 0.0-1.0

Examples:
"remind me at 9:30 every weekday to check LinkedIn perf" -> {title: "Check LinkedIn perf", schedule_type: "recurring", recurrence_rule: "weekdays", fire_time_ist: "09:30", one_off_iso_ist: null, confidence: 0.95}
"in 30 min stand up" -> {title: "Stand up", schedule_type: "one_off", recurrence_rule: null, fire_time_ist: null, one_off_iso_ist: "<now+30min in IST>", confidence: 0.9}
"call mom tomorrow 7pm" -> {title: "Call mom", schedule_type: "one_off", recurrence_rule: null, fire_time_ist: null, one_off_iso_ist: "<tomorrow 19:00 IST>", confidence: 0.9}
"daily 11pm wind down" -> {title: "Wind down", schedule_type: "recurring", recurrence_rule: "daily", fire_time_ist: "23:00", one_off_iso_ist: null, confidence: 0.95}
"""


_INTENT_SYSTEM = """\
Classify the user's message as one of:
- create_reminder: they want to set a new reminder (e.g. "remind me at 9 to X", "every weekday X", "tonight 10pm X")
- query: a question about their state — past reminders, status, what's pending, what they did. e.g. "have I checked LinkedIn today?", "what's pending?", "did I do my standup?"
- other: anything else — chitchat, instructions, complaints

Return JSON {intent, confidence}.
"""


async def classify_intent(text: str) -> IntentClassification | None:
    res = await gemini().generate_json(
        prompt=text,
        schema=IntentClassification,
        system=_INTENT_SYSTEM,
        temperature=0.0,
    )
    if not res:
        return None
    try:
        return IntentClassification.model_validate(res)
    except Exception as exc:  # noqa: BLE001
        log.warning("classify_intent.invalid_output", error=str(exc), raw=res)
        return None


async def parse_reminder(text: str) -> ParsedReminder | None:
    """Gemini-first parse with regex fallback."""
    now = now_ist()
    prompt = (
        f"current_ist={now.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"current_weekday={now.strftime('%A')}\n\n"
        f"User message: {text}"
    )
    res = await gemini().generate_json(
        prompt=prompt,
        schema=ParsedReminder,
        system=_PARSER_SYSTEM,
        temperature=0.1,
    )
    if res:
        try:
            return ParsedReminder.model_validate(res)
        except Exception as exc:  # noqa: BLE001
            log.warning("parse_reminder.invalid_output", error=str(exc), raw=res)

    # Regex fallback
    return regex_parse(text)


# ---------- regex fallback ----------

_TIME_RE = re.compile(r"\b(?:at\s+)?(\d{1,2}):(\d{2})\s*(am|pm)?\b", re.IGNORECASE)
_TIME_HOUR_AMPM_RE = re.compile(r"\b(?:at\s+)?(\d{1,2})\s*(am|pm)\b", re.IGNORECASE)
_IN_MIN_RE = re.compile(r"\bin\s+(\d{1,3})\s*(min|mins|minutes|m)\b", re.IGNORECASE)
_IN_HOUR_RE = re.compile(r"\bin\s+(\d{1,2})\s*(hour|hours|h|hr|hrs)\b", re.IGNORECASE)


def _normalize_time(h: int, m: int, ampm: str | None) -> time | None:
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
    if 0 <= h < 24 and 0 <= m < 60:
        return time(hour=h, minute=m)
    return None


def _strip_chunks(text: str, *patterns: re.Pattern[str]) -> str:
    out = text
    for p in patterns:
        out = p.sub("", out)
    return re.sub(r"\s+", " ", out).strip(" ,.;:-")


def regex_parse(text: str) -> ParsedReminder | None:
    """Minimal best-effort parse for common patterns."""
    raw = text.strip()
    if not raw:
        return None

    lower = raw.lower()

    # "in N min" / "in N hours"
    m_min = _IN_MIN_RE.search(lower)
    m_hr = _IN_HOUR_RE.search(lower)
    if m_min or m_hr:
        delta = timedelta(
            minutes=int(m_min.group(1)) if m_min else 0,
            hours=int(m_hr.group(1)) if m_hr else 0,
        )
        target = now_ist() + delta
        title = _strip_chunks(raw, _IN_MIN_RE, _IN_HOUR_RE) or "Reminder"
        title = re.sub(r"\b(remind\s+me\s+to|remind\s+me)\b", "", title, flags=re.IGNORECASE).strip()
        title = title or "Reminder"
        return ParsedReminder(
            title=title.capitalize(),
            schedule_type="one_off",
            recurrence_rule=None,
            fire_time_ist=None,
            one_off_iso_ist=target.strftime("%Y-%m-%dT%H:%M:%S"),
            confidence=0.6,
        )

    # Recurrence keywords
    rule = None
    if re.search(r"\bevery\s+weekday\b|\bweekdays?\b", lower):
        rule = "weekdays"
    elif re.search(r"\b(every\s*day|daily|each\s+day)\b", lower):
        rule = "daily"
    elif re.search(r"\b(every\s+weekend|weekends?)\b", lower):
        rule = "weekend"

    # Time of day
    t_match = _TIME_RE.search(lower)
    fire_time: time | None = None
    if t_match:
        fire_time = _normalize_time(
            int(t_match.group(1)),
            int(t_match.group(2)),
            t_match.group(3),
        )
    else:
        ah = _TIME_HOUR_AMPM_RE.search(lower)
        if ah:
            fire_time = _normalize_time(int(ah.group(1)), 0, ah.group(2))

    if rule and fire_time:
        title = _strip_chunks(raw, _TIME_RE, _TIME_HOUR_AMPM_RE)
        title = re.sub(
            r"\b(every\s+weekday|weekdays?|every\s*day|daily|each\s+day|every\s+weekend|weekends?|remind\s+me\s+to|remind\s+me)\b",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip(" ,.;:-")
        title = title or "Reminder"
        return ParsedReminder(
            title=title.capitalize(),
            schedule_type="recurring",
            recurrence_rule=rule,
            fire_time_ist=fire_time.strftime("%H:%M"),
            one_off_iso_ist=None,
            confidence=0.55,
        )

    if fire_time:
        # One-off today at HH:MM (or tomorrow if past)
        target = now_ist().replace(
            hour=fire_time.hour, minute=fire_time.minute, second=0, microsecond=0
        )
        if target <= now_ist():
            target = target + timedelta(days=1)
        title = _strip_chunks(raw, _TIME_RE, _TIME_HOUR_AMPM_RE)
        title = re.sub(
            r"\b(remind\s+me\s+to|remind\s+me|today|tonight|tomorrow)\b",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip(" ,.;:-")
        title = title or "Reminder"
        return ParsedReminder(
            title=title.capitalize(),
            schedule_type="one_off",
            recurrence_rule=None,
            fire_time_ist=None,
            one_off_iso_ist=target.strftime("%Y-%m-%dT%H:%M:%S"),
            confidence=0.5,
        )

    return None


def to_db_fields(p: ParsedReminder) -> dict:
    """Turn ParsedReminder into kwargs for create_reminder()."""
    fire_time_obj: time | None = None
    if p.fire_time_ist:
        h, m = p.fire_time_ist.split(":")
        fire_time_obj = time(int(h), int(m))

    one_off_utc = None
    if p.one_off_iso_ist:
        local = datetime.fromisoformat(p.one_off_iso_ist)
        if local.tzinfo is None:
            local = local.replace(tzinfo=IST)
        one_off_utc = to_utc(local)

    return {
        "title": p.title,
        "schedule_type": p.schedule_type,
        "recurrence_rule": p.recurrence_rule,
        "fire_time": fire_time_obj,
        "one_off_datetime": one_off_utc,
    }


# ---------- /r structured command ----------

_R_RECURRING = re.compile(
    r"^(?P<hh>\d{1,2}):(?P<mm>\d{2})\s+(?P<rule>daily|weekdays|weekend|weekly:[a-z,]+)\s+(?P<title>.+)$",
    re.IGNORECASE,
)
_R_ONCE_ABSOLUTE = re.compile(
    r"^(?P<hh>\d{1,2}):(?P<mm>\d{2})\s+once\s+(?P<title>.+)$", re.IGNORECASE
)
_R_ONCE_RELATIVE = re.compile(
    r"^in\s+(?P<n>\d{1,3})\s*(?P<unit>m|min|mins|minutes|h|hr|hrs|hour|hours)\s+(?P<title>.+)$",
    re.IGNORECASE,
)


def parse_slash_r(args: str) -> dict | None:
    """Parse `/r` arguments. Returns kwargs for create_reminder, or None."""
    args = args.strip()
    if not args:
        return None

    m = _R_RECURRING.match(args)
    if m:
        h = int(m.group("hh"))
        mm = int(m.group("mm"))
        if not (0 <= h < 24 and 0 <= mm < 60):
            return None
        rule = m.group("rule").lower()
        return {
            "title": m.group("title").strip(),
            "schedule_type": "recurring",
            "recurrence_rule": rule,
            "fire_time": time(h, mm),
            "one_off_datetime": None,
        }

    m = _R_ONCE_ABSOLUTE.match(args)
    if m:
        h = int(m.group("hh"))
        mm = int(m.group("mm"))
        if not (0 <= h < 24 and 0 <= mm < 60):
            return None
        target = now_ist().replace(hour=h, minute=mm, second=0, microsecond=0)
        if target <= now_ist():
            target += timedelta(days=1)
        return {
            "title": m.group("title").strip(),
            "schedule_type": "one_off",
            "recurrence_rule": None,
            "fire_time": None,
            "one_off_datetime": to_utc(target),
        }

    m = _R_ONCE_RELATIVE.match(args)
    if m:
        n = int(m.group("n"))
        unit = m.group("unit").lower()
        if unit.startswith("h"):
            delta = timedelta(hours=n)
        else:
            delta = timedelta(minutes=n)
        target = now_ist() + delta
        return {
            "title": m.group("title").strip(),
            "schedule_type": "one_off",
            "recurrence_rule": None,
            "fire_time": None,
            "one_off_datetime": to_utc(target),
        }

    return None
