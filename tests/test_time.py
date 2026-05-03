from __future__ import annotations

from datetime import UTC, datetime

from prometheus.utils.time import (
    IST,
    fmt_ist,
    is_in_sleep_window_ist,
    parse_recurrence,
    to_ist,
    to_utc,
)


def test_to_ist_naive_treated_as_utc():
    naive = datetime(2026, 5, 3, 12, 0, 0)
    ist = to_ist(naive)
    assert ist.tzinfo == IST
    # 12:00 UTC -> 17:30 IST
    assert ist.hour == 17 and ist.minute == 30


def test_to_utc_naive_treated_as_ist():
    naive = datetime(2026, 5, 3, 12, 0, 0)
    utc = to_utc(naive)
    assert utc.tzinfo == UTC
    # 12:00 IST -> 06:30 UTC
    assert utc.hour == 6 and utc.minute == 30


def test_fmt_ist():
    dt = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    assert fmt_ist(dt) == "17:30 IST"
    assert fmt_ist(dt, with_date=True).endswith("17:30 IST")


def test_parse_recurrence_daily():
    assert parse_recurrence("daily") == {0, 1, 2, 3, 4, 5, 6}


def test_parse_recurrence_weekdays():
    assert parse_recurrence("weekdays") == {0, 1, 2, 3, 4}


def test_parse_recurrence_weekly_specific():
    assert parse_recurrence("weekly:mon,wed,fri") == {0, 2, 4}


def test_parse_recurrence_invalid():
    assert parse_recurrence("nonsense") is None
    assert parse_recurrence(None) is None


def test_sleep_window_inside():
    midnight = datetime(2026, 5, 3, 1, 0, 0, tzinfo=IST)
    assert is_in_sleep_window_ist(midnight) is True


def test_sleep_window_edge_8_30():
    edge = datetime(2026, 5, 3, 8, 30, 0, tzinfo=IST)
    assert is_in_sleep_window_ist(edge) is False


def test_sleep_window_just_before_edge():
    just_before = datetime(2026, 5, 3, 8, 29, 0, tzinfo=IST)
    assert is_in_sleep_window_ist(just_before) is True


def test_sleep_window_outside():
    midday = datetime(2026, 5, 3, 12, 0, 0, tzinfo=IST)
    assert is_in_sleep_window_ist(midday) is False
