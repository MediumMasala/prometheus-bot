"""IST helpers + recurrence-rule evaluation.

All datetimes in DB are UTC. All datetimes shown to Yash are IST.
Container time is irrelevant — we always compute against UTC + ZoneInfo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

DAY_TOKENS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(UTC)


def fmt_ist(dt: datetime, with_date: bool = False) -> str:
    """Render in IST. '09:30 IST' or 'Mon 09:30 IST' (with_date=True)."""
    local = to_ist(dt)
    if with_date:
        return local.strftime("%a %d %b %H:%M IST")
    return local.strftime("%H:%M IST")


def parse_recurrence(rule: str | None) -> set[int] | None:
    """Return the set of weekdays (0=Mon..6=Sun) the rule fires on.

    'daily' -> {0..6}
    'weekdays' -> {0..4}
    'weekly:mon,wed,fri' -> {0,2,4}
    None / unknown -> None (caller treats as one-off / invalid)
    """
    if not rule:
        return None
    rule = rule.strip().lower()
    if rule == "daily":
        return {0, 1, 2, 3, 4, 5, 6}
    if rule == "weekdays":
        return {0, 1, 2, 3, 4}
    if rule == "weekend" or rule == "weekends":
        return {5, 6}
    if rule.startswith("weekly:"):
        days = rule.removeprefix("weekly:").split(",")
        out: set[int] = set()
        for d in days:
            d = d.strip()
            if d in DAY_TOKENS:
                out.add(DAY_TOKENS[d])
        return out or None
    return None


def next_recurring_fire_utc(
    rule: str, fire_time: time, after: datetime | None = None
) -> datetime | None:
    """Next UTC datetime when this recurring reminder should fire after `after`.

    `fire_time` is interpreted in IST. Returns None if rule is invalid.
    """
    days = parse_recurrence(rule)
    if not days:
        return None
    after = after or now_utc()
    after_ist = to_ist(after)

    candidate_today = after_ist.replace(
        hour=fire_time.hour,
        minute=fire_time.minute,
        second=0,
        microsecond=0,
    )

    for offset in range(0, 8):
        cand = candidate_today + timedelta(days=offset)
        if cand <= after_ist:
            continue
        if cand.weekday() in days:
            return to_utc(cand)
    return None


def is_in_sleep_window_ist(now: datetime | None = None) -> bool:
    """Between 00:00 and 08:30 IST inclusive on the lower bound."""
    n = to_ist(now) if now else now_ist()
    cutoff = n.replace(hour=8, minute=30, second=0, microsecond=0)
    floor = n.replace(hour=0, minute=0, second=0, microsecond=0)
    return floor <= n < cutoff


def ist_today() -> date:
    return now_ist().date()


def ist_yesterday() -> date:
    return (now_ist() - timedelta(days=1)).date()
