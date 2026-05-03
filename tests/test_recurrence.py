from __future__ import annotations

from datetime import UTC, datetime, time

from prometheus.utils.time import next_recurring_fire_utc, to_ist


def test_next_recurring_today_future():
    """Daily 09:30, current 08:00 IST -> fires same day at 09:30 IST."""
    after = datetime(2026, 5, 4, 2, 30, 0, tzinfo=UTC)  # 08:00 IST Mon
    fire_at = next_recurring_fire_utc("daily", time(9, 30), after=after)
    assert fire_at is not None
    ist = to_ist(fire_at)
    assert ist.hour == 9 and ist.minute == 30
    assert ist.year == 2026 and ist.month == 5 and ist.day == 4


def test_next_recurring_today_past_rolls_forward():
    """Daily 09:30, current 12:00 IST -> next is tomorrow 09:30."""
    after = datetime(2026, 5, 4, 6, 30, 0, tzinfo=UTC)  # 12:00 IST Mon
    fire_at = next_recurring_fire_utc("daily", time(9, 30), after=after)
    assert fire_at is not None
    ist = to_ist(fire_at)
    assert ist.day == 5


def test_next_weekdays_skips_weekend():
    """Weekdays 09:30, current Fri 12:00 IST -> next is Mon 09:30."""
    # 2026-05-08 is a Friday. 12:00 IST = 06:30 UTC.
    after = datetime(2026, 5, 8, 6, 30, 0, tzinfo=UTC)
    fire_at = next_recurring_fire_utc("weekdays", time(9, 30), after=after)
    assert fire_at is not None
    ist = to_ist(fire_at)
    assert ist.weekday() == 0  # Monday
    assert ist.hour == 9 and ist.minute == 30


def test_next_weekly_specific_days():
    """weekly:mon,wed,fri at 18:00, current Tue afternoon -> next is Wed 18:00."""
    # 2026-05-05 is Tuesday. 14:00 IST = 08:30 UTC.
    after = datetime(2026, 5, 5, 8, 30, 0, tzinfo=UTC)
    fire_at = next_recurring_fire_utc(
        "weekly:mon,wed,fri", time(18, 0), after=after
    )
    assert fire_at is not None
    ist = to_ist(fire_at)
    assert ist.weekday() == 2  # Wednesday
    assert ist.hour == 18 and ist.minute == 0


def test_invalid_rule_returns_none():
    after = datetime(2026, 5, 4, 0, 0, 0, tzinfo=UTC)
    assert next_recurring_fire_utc("nonsense", time(9, 0), after=after) is None
