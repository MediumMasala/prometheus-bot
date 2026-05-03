"""Inline button callback handlers."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

# Authz import deferred to avoid circular imports
from prometheus.bot.authz import is_authorized
from prometheus.bot.messages import (
    ALREADY_HANDLED,
    BRIEF_KILLED_ACK,
    BRIEF_RESCHED_PROMPT,
    BRIEF_TODAY_ACK,
    CANCELLED_ACK,
    DONE_ACK,
    NOTE_PROMPT,
    SLEEP_GOODNIGHT_ACK,
    SLEEP_STILL_UP_ACK,
    SNOOZE_CAP_HIT,
    SNOOZED_ACK,
    WIPE_CANCELLED,
    WIPE_DONE,
)
from prometheus.db.models import (
    ChatRole,
    FireOutcome,
    ReminderStatus,
)
from prometheus.db.repos import (
    add_chat_message,
    get_fire,
    get_reminder,
    get_user_by_telegram_id,
    set_reminder_status,
    set_sleep_state,
    wipe_user_data,
)
from prometheus.db.repos import (
    create_reminder as repo_create_reminder,
)
from prometheus.db.session import session_scope
from prometheus.scheduler.manager import (
    SNOOZE_CAP,
    cancel_nag,
    ensure_reminder_job,
    get_scheduler,
    remove_reminder_job,
    schedule_snooze_fire,
)
from prometheus.utils.logging import log
from prometheus.utils.time import (
    fmt_ist,
    now_utc,
)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return

    if not is_authorized(update.effective_user.id):
        await query.answer("Wrong door.", show_alert=True)
        return

    data = query.data
    log.info("callback.received", data=data, telegram_user_id=update.effective_user.id)

    # Always answer — Telegram will show a loading spinner otherwise
    try:
        if data.startswith("f:"):
            await _handle_fire(query, context, data)
        elif data.startswith("p:"):
            await _handle_parse(query, context, data)
        elif data.startswith("sleep:"):
            await _handle_sleep(query, context, data)
        elif data.startswith("b:"):
            await _handle_brief(query, context, data)
        elif data.startswith("wipe:"):
            await _handle_wipe(query, context, data)
        else:
            await query.answer()
    except Exception as exc:  # noqa: BLE001
        log.exception("callback.error", data=data, error=str(exc))
        await query.answer("Error logged.", show_alert=False)


# ---------------- fire callbacks ----------------


async def _handle_fire(query, context, data: str) -> None:
    parts = data.split(":")
    if len(parts) < 3:
        await query.answer()
        return
    action = parts[1]
    fire_id = int(parts[2])
    scheduler = get_scheduler()

    if action == "done":
        await _ack_and_close(query, fire_id, FireOutcome.done, DONE_ACK)
        cancel_nag(scheduler, fire_id)
    elif action == "cancel":
        await _ack_and_close(query, fire_id, FireOutcome.cancelled, CANCELLED_ACK)
        cancel_nag(scheduler, fire_id)
    elif action == "snooze":
        await _do_snooze(query, fire_id)
    elif action == "note":
        await _start_note(query, context, fire_id)
    elif action == "note_done":
        await _ack_and_close(
            query, fire_id, FireOutcome.note_added, DONE_ACK, keep_note=True
        )
        cancel_nag(scheduler, fire_id)
    elif action == "note_pending":
        await _ack_and_close(
            query, fire_id, FireOutcome.pending, "Noted. Pending.", keep_note=True
        )
        cancel_nag(scheduler, fire_id)
    else:
        await query.answer()


async def _ack_and_close(
    query, fire_id: int, outcome: FireOutcome, ack_text: str, keep_note: bool = False
) -> None:
    async with session_scope() as session:
        fire = await get_fire(session, fire_id)
        if fire is None:
            await query.answer("Gone.")
            return
        if fire.acknowledged_at is not None and outcome != FireOutcome.note_added and not keep_note:
            await query.answer(ALREADY_HANDLED)
            return
        # Pending after Add Note → still ack so nag loop stops; recap counts it as pending.
        fire.outcome = outcome
        fire.acknowledged_at = now_utc()
        rem = await get_reminder(session, fire.reminder_id) if fire.reminder_id else None
        user_id = rem.user_id if rem else None
        if user_id is not None:
            await add_chat_message(session, user_id, ChatRole.assistant, ack_text)

    # Edit the message to remove keyboard, replace with status line
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.answer(ack_text)


async def _do_snooze(query, fire_id: int) -> None:
    scheduler = get_scheduler()
    next_count = 0
    reminder_id: int | None = None
    user_id: int | None = None

    async with session_scope() as session:
        fire = await get_fire(session, fire_id)
        if fire is None:
            await query.answer("Gone.")
            return
        if fire.acknowledged_at is not None:
            await query.answer(ALREADY_HANDLED)
            return
        if fire.snooze_count >= SNOOZE_CAP:
            await query.answer(SNOOZE_CAP_HIT, show_alert=True)
            return
        fire.outcome = FireOutcome.snoozed
        fire.acknowledged_at = now_utc()
        reminder_id = fire.reminder_id
        next_count = fire.snooze_count + 1
        rem = await get_reminder(session, reminder_id)
        user_id = rem.user_id if rem else None

    cancel_nag(scheduler, fire_id)

    if reminder_id is None:
        await query.answer("Snoozed.")
        return

    when_utc = schedule_snooze_fire(
        scheduler,
        reminder_id=reminder_id,
        fire_id=fire_id,
        snooze_count=next_count,
    )
    when_str = fmt_ist(when_utc)
    ack = SNOOZED_ACK.format(when_ist=when_str)
    if user_id is not None:
        async with session_scope() as session:
            await add_chat_message(session, user_id, ChatRole.assistant, ack)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.answer(ack)


async def _start_note(query, context: ContextTypes.DEFAULT_TYPE, fire_id: int) -> None:
    context.chat_data["awaiting_note_for_fire"] = fire_id  # type: ignore[index]
    await query.answer()
    await query.message.reply_text(NOTE_PROMPT)


# ---------------- parse confirm callbacks ----------------


async def _handle_parse(query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    parts = data.split(":")
    if len(parts) < 2:
        await query.answer()
        return
    action = parts[1]
    pending = (context.chat_data or {}).get("pending_parse")  # type: ignore[union-attr]

    if action == "cancel":
        context.chat_data.pop("pending_parse", None)  # type: ignore[union-attr]
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.answer("Dropped.")
        await query.message.reply_text("Dropped.")
        return

    if action == "edit":
        context.chat_data["awaiting_parse_edit"] = True  # type: ignore[index]
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.answer()
        await query.message.reply_text("Re-describe it.")
        return

    if action == "confirm":
        if not pending:
            await query.answer("Nothing pending.")
            return
        await _commit_parsed(query, context, pending)
        return


async def _commit_parsed(query, context: ContextTypes.DEFAULT_TYPE, parsed_kwargs: dict) -> None:
    """parsed_kwargs as produced by parser.to_db_fields() + must include user_id."""
    scheduler = get_scheduler()
    rem_id: int | None = None
    summary = ""

    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, query.from_user.id)
        if user is None:
            await query.answer("User unknown.")
            return
        from prometheus.db.models import ScheduleType

        st = ScheduleType(parsed_kwargs["schedule_type"])
        reminder = await repo_create_reminder(
            session,
            user_id=user.id,
            title=parsed_kwargs["title"],
            schedule_type=st,
            recurrence_rule=parsed_kwargs.get("recurrence_rule"),
            fire_time=parsed_kwargs.get("fire_time"),
            one_off_datetime=parsed_kwargs.get("one_off_datetime"),
        )
        rem_id = reminder.id

        if st == ScheduleType.recurring:
            ftime = parsed_kwargs["fire_time"]
            summary = (
                f"Locked in. {reminder.title} — "
                f"{parsed_kwargs.get('recurrence_rule')} at "
                f"{ftime.strftime('%H:%M')} IST. Id={rem_id}."
            )
        else:
            when_ist = fmt_ist(parsed_kwargs["one_off_datetime"], with_date=True)
            summary = f"Locked in. {reminder.title} — {when_ist}. Id={rem_id}."

        await add_chat_message(session, user.id, ChatRole.assistant, summary)

        # Re-fetch and arm
        from prometheus.db.repos import get_reminder

        re_fetched = await get_reminder(session, rem_id)
        if re_fetched:
            ensure_reminder_job(scheduler, re_fetched)

    context.chat_data.pop("pending_parse", None)  # type: ignore[union-attr]
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.answer("Locked in.")
    await query.message.reply_text(summary)


# ---------------- sleep callbacks ----------------


async def _handle_sleep(query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    from prometheus.db.models import SleepState
    from prometheus.utils.time import ist_yesterday

    parts = data.split(":")
    answer = parts[1] if len(parts) > 1 else ""
    target_day = ist_yesterday()

    if answer == "yes":
        async with session_scope() as session:
            await set_sleep_state(session, target_day, SleepState.asleep)
            user = await get_user_by_telegram_id(session, query.from_user.id)
            if user:
                await add_chat_message(
                    session, user.id, ChatRole.assistant, SLEEP_GOODNIGHT_ACK
                )
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.answer(SLEEP_GOODNIGHT_ACK)
        await query.message.reply_text(SLEEP_GOODNIGHT_ACK)
    elif answer == "no":
        async with session_scope() as session:
            await set_sleep_state(session, target_day, SleepState.awake)
            user = await get_user_by_telegram_id(session, query.from_user.id)
            if user:
                await add_chat_message(
                    session, user.id, ChatRole.assistant, SLEEP_STILL_UP_ACK
                )
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.answer(SLEEP_STILL_UP_ACK)
        await query.message.reply_text(SLEEP_STILL_UP_ACK)


# ---------------- brief callbacks ----------------


async def _handle_brief(query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    parts = data.split(":")
    if len(parts) < 4:
        await query.answer()
        return
    action = parts[1]
    rem_id = int(parts[2])
    fire_id = int(parts[3])
    scheduler = get_scheduler()

    if action == "today":
        # Reuse the snooze plumbing to schedule an immediate fire
        schedule_snooze_fire(
            scheduler,
            reminder_id=rem_id,
            fire_id=fire_id,
            snooze_count=0,
            delay_minutes=0,
        )
        await query.answer(BRIEF_TODAY_ACK)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text(BRIEF_TODAY_ACK)

    elif action == "kill":
        async with session_scope() as session:
            await set_reminder_status(session, rem_id, ReminderStatus.killed)
        remove_reminder_job(scheduler, rem_id)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.answer(BRIEF_KILLED_ACK)
        await query.message.reply_text(BRIEF_KILLED_ACK)

    elif action == "resched":
        context.chat_data["awaiting_resched_for_reminder"] = rem_id  # type: ignore[index]
        await query.answer()
        await query.message.reply_text(BRIEF_RESCHED_PROMPT)


# ---------------- wipe callbacks ----------------


async def _handle_wipe(query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    scheduler = get_scheduler()

    if action == "cancel":
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.answer(WIPE_CANCELLED)
        await query.message.reply_text(WIPE_CANCELLED)
        return

    if action == "confirm":
        # Remove all reminder/nag/snooze jobs (keep system jobs)
        for job in list(scheduler.get_jobs()):
            if (
                job.id.startswith("reminder:")
                or job.id.startswith("nag:")
                or job.id.startswith("snooze:")
            ):
                try:
                    scheduler.remove_job(job.id)
                except Exception:
                    pass

        async with session_scope() as session:
            user = await get_user_by_telegram_id(session, query.from_user.id)
            if user is None:
                await query.answer("Nothing to wipe.")
                return
            counts = await wipe_user_data(session, user.id)

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.answer("Wiped.")
        await query.message.reply_text(WIPE_DONE.format(counts=counts))
        log.info("wipe.done", counts=counts)
