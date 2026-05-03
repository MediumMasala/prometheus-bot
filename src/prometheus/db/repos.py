from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from prometheus.db.models import (
    ChatMessage,
    ChatRole,
    DailyRecap,
    FireOutcome,
    MemorySummary,
    Reminder,
    ReminderFire,
    ReminderStatus,
    ScheduleType,
    SleepState,
    User,
)
from prometheus.utils.time import now_utc

# ---------- users ----------


async def get_user_by_telegram_id(
    session: AsyncSession, telegram_user_id: int
) -> User | None:
    result = await session.execute(
        select(User).where(User.telegram_user_id == telegram_user_id)
    )
    return result.scalar_one_or_none()


async def upsert_user(
    session: AsyncSession, telegram_user_id: int, name: str | None
) -> User:
    user = await get_user_by_telegram_id(session, telegram_user_id)
    if user is None:
        user = User(telegram_user_id=telegram_user_id, name=name)
        session.add(user)
        await session.flush()
        return user
    if name and user.name != name:
        user.name = name
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


# ---------- chat_messages ----------


async def add_chat_message(
    session: AsyncSession, user_id: int, role: ChatRole, content: str
) -> ChatMessage:
    msg = ChatMessage(user_id=user_id, role=role, content=content)
    session.add(msg)
    await session.flush()
    return msg


async def last_n_chat_messages(
    session: AsyncSession, user_id: int, n: int = 50
) -> list[ChatMessage]:
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id)
        .order_by(desc(ChatMessage.created_at))
        .limit(n)
    )
    return list(reversed(result.scalars().all()))


# ---------- reminders ----------


async def create_reminder(
    session: AsyncSession,
    *,
    user_id: int,
    title: str,
    schedule_type: ScheduleType,
    recurrence_rule: str | None = None,
    fire_time: time | None = None,
    one_off_datetime: datetime | None = None,
) -> Reminder:
    r = Reminder(
        user_id=user_id,
        title=title,
        schedule_type=schedule_type,
        recurrence_rule=recurrence_rule,
        fire_time=fire_time,
        one_off_datetime=one_off_datetime,
        status=ReminderStatus.active,
    )
    session.add(r)
    await session.flush()
    return r


async def get_reminder(session: AsyncSession, reminder_id: int) -> Reminder | None:
    return await session.get(Reminder, reminder_id)


async def list_active_reminders(session: AsyncSession, user_id: int) -> list[Reminder]:
    result = await session.execute(
        select(Reminder)
        .where(Reminder.user_id == user_id, Reminder.status == ReminderStatus.active)
        .order_by(Reminder.id)
    )
    return list(result.scalars().all())


async def list_active_reminders_all(session: AsyncSession) -> list[Reminder]:
    result = await session.execute(
        select(Reminder).where(Reminder.status == ReminderStatus.active)
    )
    return list(result.scalars().all())


async def set_reminder_status(
    session: AsyncSession, reminder_id: int, status: ReminderStatus
) -> Reminder | None:
    r = await get_reminder(session, reminder_id)
    if r is None:
        return None
    r.status = status
    return r


async def touch_last_fired(session: AsyncSession, reminder_id: int) -> None:
    await session.execute(
        update(Reminder)
        .where(Reminder.id == reminder_id)
        .values(last_fired_at=now_utc())
    )


# ---------- reminder_fires ----------


async def create_fire(
    session: AsyncSession,
    *,
    reminder_id: int,
    snooze_count: int = 0,
    tg_chat_id: int | None = None,
) -> ReminderFire:
    f = ReminderFire(
        reminder_id=reminder_id,
        outcome=FireOutcome.pending,
        snooze_count=snooze_count,
        tg_chat_id=tg_chat_id,
    )
    session.add(f)
    await session.flush()
    return f


async def get_fire(session: AsyncSession, fire_id: int) -> ReminderFire | None:
    return await session.get(ReminderFire, fire_id)


async def acknowledge_fire(
    session: AsyncSession,
    fire_id: int,
    outcome: FireOutcome,
    note_text: str | None = None,
) -> ReminderFire | None:
    f = await get_fire(session, fire_id)
    if f is None:
        return None
    f.acknowledged_at = now_utc()
    f.outcome = outcome
    if note_text is not None:
        f.note_text = note_text
    return f


async def pending_fires(session: AsyncSession) -> list[ReminderFire]:
    result = await session.execute(
        select(ReminderFire).where(
            ReminderFire.acknowledged_at.is_(None),
            ReminderFire.outcome == FireOutcome.pending,
        )
    )
    return list(result.scalars().all())


async def fires_for_date_ist(
    session: AsyncSession, user_id: int, ist_day: date
) -> list[ReminderFire]:
    """Fires whose IST calendar date equals ist_day."""
    from prometheus.utils.time import IST, UTC

    start_ist = datetime.combine(ist_day, time(0, 0)).replace(tzinfo=IST)
    end_ist = start_ist + timedelta(days=1)
    start_utc = start_ist.astimezone(UTC)
    end_utc = end_ist.astimezone(UTC)
    result = await session.execute(
        select(ReminderFire)
        .join(Reminder, Reminder.id == ReminderFire.reminder_id)
        .where(
            Reminder.user_id == user_id,
            ReminderFire.fired_at >= start_utc,
            ReminderFire.fired_at < end_utc,
        )
        .order_by(ReminderFire.fired_at)
    )
    return list(result.scalars().all())


async def fires_in_window(
    session: AsyncSession,
    user_id: int,
    start: datetime,
    end: datetime,
) -> list[ReminderFire]:
    result = await session.execute(
        select(ReminderFire)
        .join(Reminder, Reminder.id == ReminderFire.reminder_id)
        .where(
            Reminder.user_id == user_id,
            ReminderFire.fired_at >= start,
            ReminderFire.fired_at < end,
        )
        .order_by(ReminderFire.fired_at)
    )
    return list(result.scalars().all())


# ---------- daily_recaps ----------


async def get_recap(session: AsyncSession, ist_day: date) -> DailyRecap | None:
    return await session.get(DailyRecap, ist_day)


async def upsert_recap(
    session: AsyncSession,
    *,
    ist_day: date,
    done_count: int,
    cancelled_count: int,
    pending_count: int,
    missed_count: int,
    sleep_state: SleepState | None = None,
    recap_sent_at: datetime | None = None,
) -> DailyRecap:
    recap = await get_recap(session, ist_day)
    if recap is None:
        recap = DailyRecap(
            recap_date=ist_day,
            done_count=done_count,
            cancelled_count=cancelled_count,
            pending_count=pending_count,
            missed_count=missed_count,
            sleep_state=sleep_state or SleepState.unknown,
            recap_sent_at=recap_sent_at,
        )
        session.add(recap)
    else:
        recap.done_count = done_count
        recap.cancelled_count = cancelled_count
        recap.pending_count = pending_count
        recap.missed_count = missed_count
        if sleep_state is not None:
            recap.sleep_state = sleep_state
        if recap_sent_at is not None:
            recap.recap_sent_at = recap_sent_at
    await session.flush()
    return recap


async def set_sleep_state(
    session: AsyncSession, ist_day: date, sleep_state: SleepState
) -> None:
    recap = await get_recap(session, ist_day)
    if recap is None:
        recap = DailyRecap(recap_date=ist_day, sleep_state=sleep_state)
        session.add(recap)
    else:
        recap.sleep_state = sleep_state
    await session.flush()


# ---------- memory_summary ----------


async def latest_memory_summary(session: AsyncSession) -> MemorySummary | None:
    result = await session.execute(
        select(MemorySummary).order_by(desc(MemorySummary.generated_at)).limit(1)
    )
    return result.scalar_one_or_none()


async def add_memory_summary(
    session: AsyncSession, *, summary_text: str, covers_until: date
) -> MemorySummary:
    m = MemorySummary(summary_text=summary_text, covers_until=covers_until)
    session.add(m)
    await session.flush()
    return m


# ---------- wipe ----------


async def wipe_user_data(session: AsyncSession, user_id: int) -> dict[str, int]:
    """Delete reminders/fires/recaps/memory/chat for a user. Keep the user row."""
    counts = {}

    # Chat messages
    res = await session.execute(
        delete(ChatMessage).where(ChatMessage.user_id == user_id)
    )
    counts["chat_messages"] = res.rowcount or 0

    # Fires (cascade-delete via reminders, but explicit for count)
    res = await session.execute(
        delete(ReminderFire).where(
            ReminderFire.reminder_id.in_(
                select(Reminder.id).where(Reminder.user_id == user_id)
            )
        )
    )
    counts["reminder_fires"] = res.rowcount or 0

    res = await session.execute(delete(Reminder).where(Reminder.user_id == user_id))
    counts["reminders"] = res.rowcount or 0

    # Daily recaps + memory_summary are global (single-user bot anyway)
    res = await session.execute(delete(DailyRecap))
    counts["daily_recaps"] = res.rowcount or 0

    res = await session.execute(delete(MemorySummary))
    counts["memory_summary"] = res.rowcount or 0

    return counts
