"""APScheduler job functions. All async, all imported via dotted-path strings."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from telegram.error import TelegramError

from prometheus.bot.keyboards import (
    brief_item_keyboard,
    fire_keyboard,
    sleep_keyboard,
)
from prometheus.bot.messages import BRIEF_NO_LEFTOVERS
from prometheus.config import settings
from prometheus.db.models import (
    ChatRole,
    FireOutcome,
    ReminderStatus,
    SleepState,
)
from prometheus.db.repos import (
    add_chat_message,
    add_memory_summary,
    create_fire,
    fires_for_date_ist,
    fires_in_window,
    get_fire,
    get_recap,
    get_reminder,
    get_user,
    get_user_by_telegram_id,
    last_n_chat_messages,
    latest_memory_summary,
    list_active_reminders_all,
    touch_last_fired,
    upsert_recap,
)
from prometheus.db.repos import (
    pending_fires as repo_pending_fires,
)
from prometheus.db.session import session_scope
from prometheus.llm.memory import (
    chats_for_summary,
    fires_for_summary,
    regenerate_summary,
)
from prometheus.llm.persona import (
    generate_fire_text,
    generate_recap_text,
    hardcoded_fire_text,
    hardcoded_morning_brief,
    hardcoded_nag_edit,
    hardcoded_nag_escalated,
    hardcoded_recap,
    hardcoded_sleep_reprompt,
)
from prometheus.scheduler.manager import (
    MAX_NAGS,
    SNOOZE_CAP,
    ensure_reminder_job,
    get_app,
    get_scheduler,
    schedule_nag,
)
from prometheus.utils.logging import log
from prometheus.utils.time import (
    IST,
    UTC,
    is_in_sleep_window_ist,
    ist_today,
    ist_yesterday,
    now_utc,
)

# ---------------- fire ----------------


async def fire_reminder(
    reminder_id: int, snooze_count_override: int = 0
) -> None:
    app = get_app()
    scheduler = get_scheduler()

    text: str | None = None
    chat_id: int | None = None
    fire_id: int | None = None
    user_db_id: int | None = None
    snooze_count = snooze_count_override
    skipped_asleep = False

    try:
        async with session_scope() as session:
            reminder = await get_reminder(session, reminder_id)
            if reminder is None or reminder.status != ReminderStatus.active:
                log.info("fire.reminder_inactive", reminder_id=reminder_id)
                return
            user = await get_user(session, reminder.user_id)
            if user is None:
                log.warning("fire.no_user", reminder_id=reminder_id)
                return
            chat_id = user.telegram_user_id
            user_db_id = user.id
            title = reminder.title

            # Sleep gating
            if is_in_sleep_window_ist():
                yest = ist_yesterday()
                recap = await get_recap(session, yest)
                if recap and recap.sleep_state == SleepState.asleep:
                    fire = await create_fire(
                        session,
                        reminder_id=reminder_id,
                        snooze_count=snooze_count,
                        tg_chat_id=chat_id,
                    )
                    fire.outcome = FireOutcome.missed
                    fire.acknowledged_at = now_utc()
                    await touch_last_fired(session, reminder_id)
                    skipped_asleep = True
                    log.info(
                        "fire.skipped_asleep",
                        reminder_id=reminder_id,
                        fire_id=fire.id,
                    )
                    return

            fire = await create_fire(
                session,
                reminder_id=reminder_id,
                snooze_count=snooze_count,
                tg_chat_id=chat_id,
            )
            fire_id = fire.id
            await touch_last_fired(session, reminder_id)

            fire_time_ist = (
                reminder.fire_time.strftime("%H:%M") if reminder.fire_time else None
            )
            text = await generate_fire_text(
                title=title,
                fire_time_ist=fire_time_ist,
                recurrence=reminder.recurrence_rule,
            )
            if not text:
                text = hardcoded_fire_text(title=title, fire_time_ist=fire_time_ist)
            fire.persona_text = text
            await add_chat_message(session, user_db_id, ChatRole.assistant, text)

        if skipped_asleep or text is None or chat_id is None or fire_id is None:
            return

        msg = await app.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=fire_keyboard(fire_id, allow_snooze=snooze_count < SNOOZE_CAP),
        )

        async with session_scope() as session:
            f2 = await get_fire(session, fire_id)
            if f2:
                f2.tg_message_id = msg.message_id

        schedule_nag(scheduler, fire_id=fire_id)
        log.info("fire.sent", reminder_id=reminder_id, fire_id=fire_id)

    except Exception as exc:  # noqa: BLE001
        log.exception("fire.error", reminder_id=reminder_id, error=str(exc))
        await _notify_owner_error("fire_reminder failed", reminder_id, exc)


# ---------------- nag ----------------


async def nag(fire_id: int) -> None:
    app = get_app()
    scheduler = get_scheduler()

    do_send = False
    do_edit = False
    new_text: str | None = None
    chat_id: int | None = None
    msg_id: int | None = None
    title: str | None = None
    nag_num = 0
    schedule_next = False
    allow_snooze = False
    user_db_id: int | None = None

    try:
        async with session_scope() as session:
            fire = await get_fire(session, fire_id)
            if (
                fire is None
                or fire.acknowledged_at is not None
                or fire.outcome != FireOutcome.pending
                or fire.nag_count >= MAX_NAGS
            ):
                return

            reminder = await get_reminder(session, fire.reminder_id)
            if reminder is None:
                return
            title = reminder.title
            user_db_id = reminder.user_id
            chat_id = fire.tg_chat_id
            msg_id = fire.tg_message_id
            nag_num = fire.nag_count + 1
            allow_snooze = fire.snooze_count < SNOOZE_CAP

            if nag_num <= 2:
                new_text = hardcoded_nag_edit(title, nag_num)
                do_edit = True
            else:
                new_text = hardcoded_nag_escalated(title)
                do_send = True

            fire.nag_count = nag_num
            fire.last_nag_at = now_utc()
            schedule_next = nag_num < MAX_NAGS
            await add_chat_message(session, user_db_id, ChatRole.assistant, new_text)

        if not chat_id or new_text is None:
            return

        keyboard = fire_keyboard(fire_id, allow_snooze=allow_snooze)
        new_msg_id: int | None = None
        if do_edit and msg_id is not None:
            try:
                await app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=new_text,
                    reply_markup=keyboard,
                )
            except TelegramError as e:
                log.warning("nag.edit_failed", fire_id=fire_id, error=str(e))
        elif do_send:
            sent = await app.bot.send_message(
                chat_id=chat_id, text=new_text, reply_markup=keyboard
            )
            new_msg_id = sent.message_id

        if new_msg_id is not None:
            async with session_scope() as session:
                f2 = await get_fire(session, fire_id)
                if f2:
                    f2.tg_message_id = new_msg_id

        if schedule_next:
            schedule_nag(scheduler, fire_id=fire_id)

        log.info("nag.sent", fire_id=fire_id, nag_num=nag_num, escalated=do_send)

    except Exception as exc:  # noqa: BLE001
        log.exception("nag.error", fire_id=fire_id, error=str(exc))


# ---------------- midnight recap ----------------


async def midnight_recap() -> None:
    app = get_app()
    try:
        owner = settings.owner_telegram_user_id
        if owner is None:
            log.warning("recap.no_owner_set")
            return

        target_day = ist_yesterday()
        text: str | None = None

        async with session_scope() as session:
            user = await get_user_by_telegram_id(session, owner)
            if user is None:
                log.warning("recap.owner_not_in_db")
                return

            fires = await fires_for_date_ist(session, user.id, target_day)
            done = sum(1 for f in fires if f.outcome == FireOutcome.done)
            cancelled = sum(1 for f in fires if f.outcome == FireOutcome.cancelled)
            note_added = sum(1 for f in fires if f.outcome == FireOutcome.note_added)
            missed = sum(1 for f in fires if f.outcome == FireOutcome.missed)
            # Pending+ack'd (e.g. user tapped "Pending" after Add Note) — count
            # toward pending in the recap, but don't sweep them.
            pending = sum(
                1
                for f in fires
                if f.outcome == FireOutcome.pending and f.acknowledged_at is not None
            )

            # Sweep stale pending → missed
            for f in fires:
                if f.outcome == FireOutcome.pending and f.acknowledged_at is None:
                    f.outcome = FireOutcome.missed
                    f.acknowledged_at = now_utc()
                    missed += 1

            done_total = done + note_added

            text = await generate_recap_text(
                done=done_total,
                cancelled=cancelled,
                pending=pending,
                missed=missed,
            )
            if not text:
                text = hardcoded_recap(done_total, cancelled, pending, missed)

            await upsert_recap(
                session,
                ist_day=target_day,
                done_count=done_total,
                cancelled_count=cancelled,
                pending_count=pending,
                missed_count=missed,
                recap_sent_at=now_utc(),
            )
            await add_chat_message(session, user.id, ChatRole.assistant, text)

        await app.bot.send_message(
            chat_id=owner, text=text, reply_markup=sleep_keyboard()
        )
        log.info("recap.sent", date=target_day.isoformat())
    except Exception as exc:  # noqa: BLE001
        log.exception("recap.error", error=str(exc))


# ---------------- morning brief ----------------


async def morning_brief() -> None:
    app = get_app()
    try:
        owner = settings.owner_telegram_user_id
        if owner is None:
            return

        leftover_items: list[tuple[int, int, str, str]] = []  # (rem_id, fire_id, title, label)
        header_text = ""
        empty = False

        async with session_scope() as session:
            user = await get_user_by_telegram_id(session, owner)
            if user is None:
                return
            yest = ist_yesterday()
            fires = await fires_for_date_ist(session, user.id, yest)
            leftovers = [
                f
                for f in fires
                if f.outcome in (FireOutcome.missed, FireOutcome.pending)
                or (f.outcome == FireOutcome.note_added and f.note_text)
            ]

            if not leftovers:
                empty = True
                await add_chat_message(
                    session, user.id, ChatRole.assistant, BRIEF_NO_LEFTOVERS
                )
            else:
                items_for_text = []
                for f in leftovers:
                    rem = await get_reminder(session, f.reminder_id)
                    if rem is None:
                        continue
                    label = (
                        f.outcome.value
                        if f.outcome != FireOutcome.note_added
                        else f"note: {(f.note_text or '')[:60]}"
                    )
                    items_for_text.append((rem.id, rem.title, label))
                    leftover_items.append((rem.id, f.id, rem.title, label))
                header_text = hardcoded_morning_brief(items_for_text)
                await add_chat_message(
                    session, user.id, ChatRole.assistant, header_text
                )

        if empty:
            await app.bot.send_message(chat_id=owner, text=BRIEF_NO_LEFTOVERS)
            log.info("brief.empty")
            return

        await app.bot.send_message(chat_id=owner, text=header_text)
        for rem_id, f_id, title, _label in leftover_items:
            await app.bot.send_message(
                chat_id=owner,
                text=f"• {title}",
                reply_markup=brief_item_keyboard(rem_id, f_id),
            )
        log.info("brief.sent", n=len(leftover_items))
    except Exception as exc:  # noqa: BLE001
        log.exception("brief.error", error=str(exc))


# ---------------- sleep re-prompt ----------------


async def sleep_reprompt() -> None:
    app = get_app()
    try:
        owner = settings.owner_telegram_user_id
        if owner is None:
            return
        target_day = ist_yesterday()
        async with session_scope() as session:
            recap = await get_recap(session, target_day)
            if recap is None or recap.sleep_state == SleepState.asleep:
                return
        await app.bot.send_message(
            chat_id=owner,
            text=hardcoded_sleep_reprompt(),
            reply_markup=sleep_keyboard(),
        )
        log.info("sleep.reprompt_sent")
    except Exception as exc:  # noqa: BLE001
        log.exception("sleep.reprompt_error", error=str(exc))


# ---------------- weekly memory regen ----------------


async def weekly_memory_regen() -> None:
    try:
        owner = settings.owner_telegram_user_id
        if owner is None:
            return

        async with session_scope() as session:
            user = await get_user_by_telegram_id(session, owner)
            if user is None:
                return

            prev = await latest_memory_summary(session)
            if prev is not None:
                start_dt = (
                    datetime.combine(prev.covers_until, time(0, 0))
                    .replace(tzinfo=IST)
                    .astimezone(UTC)
                )
            else:
                start_dt = now_utc() - timedelta(days=14)

            fires = await fires_in_window(session, user.id, start_dt, now_utc())
            msgs = await last_n_chat_messages(session, user.id, 200)
            msgs_in_window = [m for m in msgs if m.created_at >= start_dt]

            summary = await regenerate_summary(
                fires_window=fires_for_summary(fires),
                chat_window=chats_for_summary(msgs_in_window),
                prev_summary=prev.summary_text if prev else None,
            )
            if summary:
                await add_memory_summary(
                    session, summary_text=summary, covers_until=ist_today()
                )
                log.info("memory.regenerated", chars=len(summary))
            else:
                log.warning("memory.regen_skipped_no_llm_or_empty")
    except Exception as exc:  # noqa: BLE001
        log.exception("memory.regen_error", error=str(exc))


# ---------------- error notifier ----------------


async def _notify_owner_error(prefix: str, ctx: object, exc: Exception) -> None:
    if settings.owner_telegram_user_id is None:
        return
    try:
        app = get_app()
        msg = (
            f"⚠ Prometheus error\n"
            f"{prefix} (ctx={ctx})\n"
            f"{type(exc).__name__}: {exc}"
        )
        await app.bot.send_message(
            chat_id=settings.owner_telegram_user_id, text=msg[:3500]
        )
    except Exception as send_err:  # noqa: BLE001
        log.error("notify_owner.failed", error=str(send_err))


# ---------------- recovery on boot ----------------


async def recover_state() -> None:
    """On boot: re-arm reminder cron jobs + reschedule nags for unack'd fires."""
    scheduler = get_scheduler()
    n_rem = 0
    n_pending = 0
    async with session_scope() as session:
        reminders = await list_active_reminders_all(session)
        for r in reminders:
            ensure_reminder_job(scheduler, r)
        n_rem = len(reminders)

        pending = await repo_pending_fires(session)
        for f in pending:
            if f.nag_count >= MAX_NAGS:
                continue
            schedule_nag(scheduler, fire_id=f.id, delay_seconds=60)
        n_pending = len(pending)

    log.info("scheduler.recovered", reminders=n_rem, pending_fires=n_pending)
