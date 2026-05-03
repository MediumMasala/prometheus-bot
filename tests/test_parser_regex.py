from __future__ import annotations

from datetime import time

from prometheus.llm.parser import parse_slash_r, regex_parse

# ---------- /r structured ----------


def test_slash_r_recurring_weekdays():
    out = parse_slash_r("09:30 weekdays Check LinkedIn perf")
    assert out is not None
    assert out["schedule_type"] == "recurring"
    assert out["recurrence_rule"] == "weekdays"
    assert out["fire_time"] == time(9, 30)
    assert out["title"] == "Check LinkedIn perf"


def test_slash_r_recurring_daily():
    out = parse_slash_r("23:00 daily Wind down")
    assert out is not None
    assert out["recurrence_rule"] == "daily"
    assert out["fire_time"] == time(23, 0)


def test_slash_r_weekly_specific():
    out = parse_slash_r("18:00 weekly:mon,wed,fri Standup with X")
    assert out is not None
    assert out["recurrence_rule"] == "weekly:mon,wed,fri"
    assert out["fire_time"] == time(18, 0)
    assert "Standup with X" in out["title"]


def test_slash_r_once_absolute():
    out = parse_slash_r("21:30 once Buy milk")
    assert out is not None
    assert out["schedule_type"] == "one_off"
    assert out["recurrence_rule"] is None
    assert out["one_off_datetime"] is not None
    assert out["title"] == "Buy milk"


def test_slash_r_once_relative_min():
    out = parse_slash_r("in 30 min Quick call")
    assert out is not None
    assert out["schedule_type"] == "one_off"
    assert out["one_off_datetime"] is not None


def test_slash_r_once_relative_hours():
    out = parse_slash_r("in 2 hours Standup")
    assert out is not None
    assert out["schedule_type"] == "one_off"


def test_slash_r_invalid_time():
    assert parse_slash_r("25:00 daily Wrong time") is None


def test_slash_r_empty():
    assert parse_slash_r("") is None


# ---------- regex fallback (free-text) ----------


def test_regex_in_30_min():
    out = regex_parse("in 30 min stand up")
    assert out is not None
    assert out.schedule_type == "one_off"
    assert out.one_off_iso_ist is not None
    assert "stand up".lower() in out.title.lower()


def test_regex_every_weekday():
    out = regex_parse("every weekday at 09:30 check linkedin")
    assert out is not None
    assert out.schedule_type == "recurring"
    assert out.recurrence_rule == "weekdays"
    assert out.fire_time_ist == "09:30"


def test_regex_daily_at_time():
    out = regex_parse("daily at 11pm wind down")
    assert out is not None
    assert out.schedule_type == "recurring"
    assert out.recurrence_rule == "daily"
    assert out.fire_time_ist == "23:00"


def test_regex_one_off_at_time():
    out = regex_parse("at 7pm call mom")
    assert out is not None
    assert out.schedule_type == "one_off"


def test_regex_unparseable():
    assert regex_parse("hello there how are you") is None
