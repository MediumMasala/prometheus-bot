"""InlineKeyboardMarkup builders. Emoji allowed in button labels per spec."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def fire_keyboard(fire_id: int, *, allow_snooze: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("✅ Done", callback_data=f"f:done:{fire_id}"),
            InlineKeyboardButton("✖ Cancel", callback_data=f"f:cancel:{fire_id}"),
        ],
    ]
    second_row: list[InlineKeyboardButton] = []
    if allow_snooze:
        second_row.append(
            InlineKeyboardButton("🌙 Snooze 30", callback_data=f"f:snooze:{fire_id}")
        )
    second_row.append(
        InlineKeyboardButton("✏️ Add Note", callback_data=f"f:note:{fire_id}")
    )
    rows.append(second_row)
    return InlineKeyboardMarkup(rows)


def note_followup_keyboard(fire_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Done", callback_data=f"f:note_done:{fire_id}"),
                InlineKeyboardButton("⏳ Pending", callback_data=f"f:note_pending:{fire_id}"),
            ]
        ]
    )


def parse_confirm_keyboard(cache_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=f"p:confirm:{cache_key}"),
                InlineKeyboardButton("Edit", callback_data=f"p:edit:{cache_key}"),
                InlineKeyboardButton("Cancel", callback_data=f"p:cancel:{cache_key}"),
            ]
        ]
    )


def sleep_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Yes, goodnight", callback_data="sleep:yes"),
                InlineKeyboardButton("Still up", callback_data="sleep:no"),
            ]
        ]
    )


def brief_item_keyboard(reminder_id: int, fire_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Today", callback_data=f"b:today:{reminder_id}:{fire_id}"
                ),
                InlineKeyboardButton(
                    "Kill", callback_data=f"b:kill:{reminder_id}:{fire_id}"
                ),
                InlineKeyboardButton(
                    "Reschedule", callback_data=f"b:resched:{reminder_id}:{fire_id}"
                ),
            ]
        ]
    )


def wipe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm wipe", callback_data="wipe:confirm"),
                InlineKeyboardButton("Cancel", callback_data="wipe:cancel"),
            ]
        ]
    )
