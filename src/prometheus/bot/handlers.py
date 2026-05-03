from __future__ import annotations

import json
from datetime import time

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from prometheus.bot.authz import is_authorized
from prometheus.bot.callbacks import on_callback
from prometheus.bot.keyboards import (
    note_followup_keyboard,
    parse_confirm_keyboard,
    wipe_keyboard,
)
from prometheus.bot.messages import (
    ASK_NAME,
    KILL_NOT_FOUND,
    KILL_OK,
    KILL_USAGE,
    LIST_EMPTY,
    LIST_HEADER,
    NOT_AUTHORIZED,
    NOTE_NO_PENDING,
    NOTE_SAVED_PICK_OUTCOME,
    ONBOARDING_NEW_USER,
    ONBOARDING_RETURNING_USER,
    PARSE_CLARIFIER,
    PARSE_CONFIRM_HEADER,
    PARSE_FAILED,
    PARSE_LOW_CONFIDENCE_FORCED,
    PAUSE_NOT_FOUND,
    PAUSE_OK,
    PAUSE_USAGE,
    QUERY_LLM_DOWN,
    R_CREATED_ONEOFF,
    R_CREATED_RECURRING,
    R_USAGE,
    RESUME_NOT_FOUND,
    RESUME_OK,
    RESUME_USAGE,
    SEED_FAIL,
    SEED_NO_FILE,
    SEED_OK,
    START_FIRST,
    WIPE_PROMPT,
)
from prometheus.db.models import ChatRole, ReminderStatus, ScheduleType
from prometheus.db.repos import (
    add_chat_message,
    get_reminder,
    get_user_by_telegram_id,
    list_active_reminders,
    set_reminder_status,
    upsert_user,
)
from prometheus.db.repos import (
    create_reminder as repo_create_reminder,
)
from prometheus.db.session import session_scope
from prometheus.llm.memory import build_working_memory, memory_to_blob
from prometheus.llm.parser import (
    classify_intent,
    parse_reminder,
    parse_slash_r,
    to_db_fields,
)
from prometheus.llm.persona import (
    generate_query_response,
    hardcoded_intent_other,
)
from prometheus.scheduler.manager import (
    ensure_reminder_job,
    get_scheduler,
    remove_reminder_job,
)
from prometheus.utils.logging import log
from prometheus.utils.time import fmt_ist, now_utc, shift_past_to_future

# ============ commands ============


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    if tg_user is None or update.message is None:
        return

    log.info("cmd.start", telegram_user_id=tg_user.id, username=tg_user.username)

    if not is_authorized(tg_user.id):
        log.warning("auth.denied", telegram_user_id=tg_user.id)
        await update.message.reply_text(NOT_AUTHORIZED)
        return

    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, tg_user.id)
        if user is not None and user.name:
            reply = ONBOARDING_RETURNING_USER
        else:
            if user is None:
                user = await upsert_user(session, tg_user.id, name=None)
                log.info("user.created", user_id=user.id)
            reply = ASK_NAME
        await add_chat_message(session, user.id, ChatRole.assistant, reply)

    await update.message.reply_text(reply)


async def cmd_r(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _gated(update):
        await update.message.reply_text(NOT_AUTHORIZED)
        return
    if update.message is None:
        return

    args_text = " ".join(context.args) if context.args else ""
    if not args_text:
        await update.message.reply_text(R_USAGE)
        return

    parsed = parse_slash_r(args_text)
    if parsed is None:
        await update.message.reply_text(R_USAGE)
        return

    await _create_and_arm(update, parsed)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _gated(update):
        await update.message.reply_text(NOT_AUTHORIZED)
        return
    if update.message is None:
        return

    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, update.effective_user.id)
        if user is None:
            await update.message.reply_text(START_FIRST)
            return
        reminders = await list_active_reminders(session, user.id)

    if not reminders:
        await update.message.reply_text(LIST_EMPTY)
        return

    lines = [LIST_HEADER]
    for r in reminders:
        if r.schedule_type == ScheduleType.recurring:
            ft = r.fire_time.strftime("%H:%M") if r.fire_time else "?"
            lines.append(
                f"{r.id}. {r.title} — {r.recurrence_rule} at {ft} IST"
            )
        else:
            when = (
                fmt_ist(r.one_off_datetime, with_date=True)
                if r.one_off_datetime
                else "?"
            )
            lines.append(f"{r.id}. {r.title} — once at {when}")
    await update.message.reply_text("\n".join(lines))


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _gated(update):
        await update.message.reply_text(NOT_AUTHORIZED)
        return
    if update.message is None:
        return
    if not context.args:
        await update.message.reply_text(KILL_USAGE)
        return
    try:
        rid = int(context.args[0])
    except ValueError:
        await update.message.reply_text(KILL_USAGE)
        return

    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, update.effective_user.id)
        if user is None:
            await update.message.reply_text(START_FIRST)
            return
        rem = await get_reminder(session, rid)
        if rem is None or rem.user_id != user.id:
            await update.message.reply_text(KILL_NOT_FOUND)
            return
        await set_reminder_status(session, rid, ReminderStatus.killed)

    remove_reminder_job(get_scheduler(), rid)
    await update.message.reply_text(KILL_OK.format(id=rid))
    log.info("reminder.killed", reminder_id=rid)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _gated(update):
        await update.message.reply_text(NOT_AUTHORIZED)
        return
    if update.message is None:
        return
    if not context.args:
        await update.message.reply_text(PAUSE_USAGE)
        return
    try:
        rid = int(context.args[0])
    except ValueError:
        await update.message.reply_text(PAUSE_USAGE)
        return

    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, update.effective_user.id)
        if user is None:
            await update.message.reply_text(START_FIRST)
            return
        rem = await get_reminder(session, rid)
        if rem is None or rem.user_id != user.id:
            await update.message.reply_text(PAUSE_NOT_FOUND)
            return
        await set_reminder_status(session, rid, ReminderStatus.paused)

    remove_reminder_job(get_scheduler(), rid)
    await update.message.reply_text(PAUSE_OK.format(id=rid))


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _gated(update):
        await update.message.reply_text(NOT_AUTHORIZED)
        return
    if update.message is None:
        return
    if not context.args:
        await update.message.reply_text(RESUME_USAGE)
        return
    try:
        rid = int(context.args[0])
    except ValueError:
        await update.message.reply_text(RESUME_USAGE)
        return

    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, update.effective_user.id)
        if user is None:
            await update.message.reply_text(START_FIRST)
            return
        rem = await get_reminder(session, rid)
        if rem is None or rem.user_id != user.id:
            await update.message.reply_text(RESUME_NOT_FOUND)
            return
        await set_reminder_status(session, rid, ReminderStatus.active)
        ensure_reminder_job(get_scheduler(), rem)

    await update.message.reply_text(RESUME_OK.format(id=rid))


async def cmd_wipe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _gated(update):
        await update.message.reply_text(NOT_AUTHORIZED)
        return
    if update.message is None:
        return
    await update.message.reply_text(WIPE_PROMPT, reply_markup=wipe_keyboard())


async def cmd_seed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _gated(update):
        await update.message.reply_text(NOT_AUTHORIZED)
        return
    if update.message is None:
        return

    from pathlib import Path

    seed_path = Path("seeds/default.json")
    if not seed_path.exists():
        await update.message.reply_text(SEED_NO_FILE)
        return

    try:
        data = json.loads(seed_path.read_text())
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(SEED_FAIL.format(error=str(exc)))
        return

    n = 0
    scheduler = get_scheduler()
    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, update.effective_user.id)
        if user is None:
            await update.message.reply_text(START_FIRST)
            return

        for item in data:
            try:
                ft = (
                    time(*[int(x) for x in item["fire_time"].split(":")])
                    if item.get("fire_time")
                    else None
                )
                rem = await repo_create_reminder(
                    session,
                    user_id=user.id,
                    title=item["title"],
                    schedule_type=ScheduleType(item["schedule_type"]),
                    recurrence_rule=item.get("recurrence_rule"),
                    fire_time=ft,
                    one_off_datetime=None,
                )
                ensure_reminder_job(scheduler, rem)
                n += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("seed.item_failed", item=item, error=str(exc))

    await update.message.reply_text(SEED_OK.format(n=n))


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _gated(update):
        return
    if update.message is None:
        return
    scheduler = get_scheduler()
    n_jobs = len(scheduler.get_jobs())
    await update.message.reply_text(f"OK. scheduler={scheduler.running}, jobs={n_jobs}.")


# ============ free-text router ============


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    if tg_user is None or update.message is None or update.message.text is None:
        return

    if not is_authorized(tg_user.id):
        await update.message.reply_text(NOT_AUTHORIZED)
        return

    text = update.message.text.strip()
    if not text:
        return

    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, tg_user.id)
        if user is None:
            await update.message.reply_text(START_FIRST)
            return
        await add_chat_message(session, user.id, ChatRole.user, text)

        # 1) onboarding name capture
        if user.name is None:
            user.name = text[:120]
            log.info("user.named", user_id=user.id, name=user.name)
            reply = ONBOARDING_NEW_USER
            await add_chat_message(session, user.id, ChatRole.assistant, reply)
            await update.message.reply_text(reply)
            return

    # Outside session_scope for the branches below — they may open new sessions

    # 2) note capture
    fire_id_for_note = (context.chat_data or {}).get("awaiting_note_for_fire")
    if fire_id_for_note:
        await _capture_note(update, context, fire_id_for_note, text)
        return

    # 3) reschedule capture
    rid_for_resched = (context.chat_data or {}).get("awaiting_resched_for_reminder")
    if rid_for_resched:
        await _capture_resched(update, context, rid_for_resched, text)
        return

    # 4) parse-edit re-prompt
    if (context.chat_data or {}).get("awaiting_parse_edit"):
        context.chat_data.pop("awaiting_parse_edit", None)
        await _try_parse_and_confirm(update, context, text, force_create=False)
        return

    # 5) low-confidence clarifier reply
    pending_clarifier = (context.chat_data or {}).get("awaiting_parse_clarifier")
    if pending_clarifier:
        combined = f"{pending_clarifier} ; {text}"
        context.chat_data.pop("awaiting_parse_clarifier", None)
        await _try_parse_and_confirm(
            update, context, combined, force_create=True
        )
        return

    # 6) intent classification
    intent = await classify_intent(text)

    if intent is None:
        # No LLM available → assume reminder creation
        await _try_parse_and_confirm(update, context, text, force_create=False)
        return

    if intent.intent == "create_reminder":
        await _try_parse_and_confirm(update, context, text, force_create=False)
        return

    if intent.intent == "query":
        await _answer_query(update, context, text)
        return

    # 'other'
    reply = hardcoded_intent_other()
    async with session_scope() as session:
        u = await get_user_by_telegram_id(session, tg_user.id)
        if u:
            await add_chat_message(session, u.id, ChatRole.assistant, reply)
    await update.message.reply_text(reply)


# ============ free-text helpers ============


async def _capture_note(update, context, fire_id: int, note_text: str) -> None:
    from prometheus.db.repos import get_fire

    async with session_scope() as session:
        fire = await get_fire(session, fire_id)
        user = await get_user_by_telegram_id(session, update.effective_user.id)
        if fire is None:
            await update.message.reply_text(NOTE_NO_PENDING)
            context.chat_data.pop("awaiting_note_for_fire", None)
            return
        fire.note_text = note_text
        if user is not None:
            await add_chat_message(
                session,
                user.id,
                ChatRole.assistant,
                NOTE_SAVED_PICK_OUTCOME,
            )

    context.chat_data.pop("awaiting_note_for_fire", None)
    await update.message.reply_text(
        NOTE_SAVED_PICK_OUTCOME, reply_markup=note_followup_keyboard(fire_id)
    )


async def _capture_resched(update, context, reminder_id: int, text: str) -> None:
    """User typed a new schedule for an existing reminder."""
    from prometheus.db.repos import get_reminder

    async with session_scope() as session:
        rem = await get_reminder(session, reminder_id)
        if rem is None:
            await update.message.reply_text("Reminder is gone.")
            context.chat_data.pop("awaiting_resched_for_reminder", None)
            return

    parsed = parse_slash_r(text)
    if parsed is None:
        parsed_p = await parse_reminder(text)
        if parsed_p is not None:
            parsed = to_db_fields(parsed_p)

    if parsed is None:
        await update.message.reply_text(
            "Couldn't parse the new schedule. Try `/r 09:00 weekdays Title`."
        )
        return

    async with session_scope() as session:
        rem = await get_reminder(session, reminder_id)
        if rem is None:
            await update.message.reply_text("Reminder is gone.")
            return
        rem.schedule_type = ScheduleType(parsed["schedule_type"])
        rem.recurrence_rule = parsed.get("recurrence_rule")
        rem.fire_time = parsed.get("fire_time")
        rem.one_off_datetime = parsed.get("one_off_datetime")
        rem.status = ReminderStatus.active

    async with session_scope() as session:
        rem = await get_reminder(session, reminder_id)
        if rem is None:
            return
        ensure_reminder_job(get_scheduler(), rem)

    context.chat_data.pop("awaiting_resched_for_reminder", None)
    await update.message.reply_text(f"Rescheduled #{reminder_id}.")


async def _try_parse_and_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, *, force_create: bool
) -> None:
    from datetime import timedelta

    parsed_p = await parse_reminder(text)
    if parsed_p is None:
        await update.message.reply_text(PARSE_FAILED)
        return

    if parsed_p.confidence < 0.7 and not force_create:
        # Ask one clarifier
        context.chat_data["awaiting_parse_clarifier"] = text  # type: ignore[index]
        await update.message.reply_text(PARSE_CLARIFIER)
        return

    db_kwargs = to_db_fields(parsed_p)

    # Auto-shift past one-offs (yesterday/last hour) to the next future
    # occurrence at the same wall-clock time.
    shifted_note = ""
    one_off = db_kwargs.get("one_off_datetime")
    if one_off and one_off < now_utc() - timedelta(minutes=5):
        new_dt = shift_past_to_future(one_off)
        db_kwargs["one_off_datetime"] = new_dt
        shifted_note = (
            f"\n(That time was past — moved to {fmt_ist(new_dt, with_date=True)}.)"
        )

    if parsed_p.confidence < 0.7 and force_create:
        # commit best guess + warn
        await _create_and_arm(update, db_kwargs, low_conf=True, extra_note=shifted_note)
        return

    summary = _format_parse_summary(parsed_p, db_kwargs)
    context.chat_data["pending_parse"] = db_kwargs  # type: ignore[index]

    await update.message.reply_text(
        f"{PARSE_CONFIRM_HEADER}\n{summary}{shifted_note}",
        reply_markup=parse_confirm_keyboard("current"),
    )


def _format_parse_summary(parsed, db_kwargs: dict) -> str:
    if parsed.schedule_type == "recurring":
        return (
            f"{db_kwargs['title']} — {db_kwargs['recurrence_rule']} "
            f"at {db_kwargs['fire_time'].strftime('%H:%M') if db_kwargs['fire_time'] else '?'} IST"
        )
    when = (
        fmt_ist(db_kwargs["one_off_datetime"], with_date=True)
        if db_kwargs.get("one_off_datetime")
        else "?"
    )
    return f"{db_kwargs['title']} — once at {when}"


async def _create_and_arm(
    update: Update,
    db_kwargs: dict,
    *,
    low_conf: bool = False,
    extra_note: str = "",
) -> None:
    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, update.effective_user.id)
        if user is None:
            await update.message.reply_text(START_FIRST)
            return
        rem = await repo_create_reminder(
            session,
            user_id=user.id,
            title=db_kwargs["title"],
            schedule_type=ScheduleType(db_kwargs["schedule_type"]),
            recurrence_rule=db_kwargs.get("recurrence_rule"),
            fire_time=db_kwargs.get("fire_time"),
            one_off_datetime=db_kwargs.get("one_off_datetime"),
        )
        rid = rem.id
        if rem.schedule_type == ScheduleType.recurring:
            msg = R_CREATED_RECURRING.format(
                title=rem.title,
                rule=rem.recurrence_rule,
                time_ist=rem.fire_time.strftime("%H:%M") if rem.fire_time else "?",
                id=rid,
            )
        else:
            when = (
                fmt_ist(rem.one_off_datetime, with_date=True)
                if rem.one_off_datetime
                else "?"
            )
            msg = R_CREATED_ONEOFF.format(title=rem.title, when_ist=when, id=rid)

        if low_conf:
            msg = f"{msg}\n{PARSE_LOW_CONFIDENCE_FORCED}"
        if extra_note:
            msg = f"{msg}{extra_note}"

        await add_chat_message(session, user.id, ChatRole.assistant, msg)

    async with session_scope() as session:
        rem = await get_reminder(session, rid)
        if rem:
            ensure_reminder_job(get_scheduler(), rem)

    await update.message.reply_text(msg)
    log.info("reminder.created", reminder_id=rid, low_conf=low_conf)


async def _answer_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE, question: str
) -> None:
    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, update.effective_user.id)
        if user is None:
            return
        memory = await build_working_memory(session, user.id)

    blob = memory_to_blob(memory)
    answer = await generate_query_response(question=question, memory_blob=blob)
    if not answer:
        answer = QUERY_LLM_DOWN

    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, update.effective_user.id)
        if user:
            await add_chat_message(session, user.id, ChatRole.assistant, answer)

    await update.message.reply_text(answer)


def _gated(update: Update) -> bool:
    return (
        update.effective_user is not None
        and is_authorized(update.effective_user.id)
    )


# ============ registration ============


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("r", cmd_r))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("kill", cmd_kill))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("wipe", cmd_wipe))
    app.add_handler(CommandHandler("seed", cmd_seed))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
