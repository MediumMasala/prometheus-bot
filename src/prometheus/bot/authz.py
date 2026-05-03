"""Owner-lockdown helper, isolated to break import cycles."""

from __future__ import annotations

from prometheus.config import settings


def is_authorized(telegram_user_id: int) -> bool:
    if settings.owner_telegram_user_id is None:
        return True
    return telegram_user_id == settings.owner_telegram_user_id
