from __future__ import annotations

from unittest.mock import patch

from prometheus.bot.authz import is_authorized


def test_authorized_when_no_owner_set():
    with patch("prometheus.bot.authz.settings") as mock_settings:
        mock_settings.owner_telegram_user_id = None
        assert is_authorized(12345) is True
        assert is_authorized(99999) is True


def test_authorized_only_owner_when_set():
    with patch("prometheus.bot.authz.settings") as mock_settings:
        mock_settings.owner_telegram_user_id = 999
        assert is_authorized(999) is True
        assert is_authorized(12345) is False
        assert is_authorized(0) is False
