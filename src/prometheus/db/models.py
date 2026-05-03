from __future__ import annotations

import enum
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _values(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


class ChatRole(enum.StrEnum):
    user = "user"
    assistant = "assistant"


class ScheduleType(enum.StrEnum):
    one_off = "one_off"
    recurring = "recurring"


class ReminderStatus(enum.StrEnum):
    active = "active"
    paused = "paused"
    killed = "killed"


class FireOutcome(enum.StrEnum):
    pending = "pending"
    done = "done"
    cancelled = "cancelled"
    snoozed = "snoozed"
    missed = "missed"
    note_added = "note_added"


class SleepState(enum.StrEnum):
    unknown = "unknown"
    awake = "awake"
    asleep = "asleep"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chat_messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    reminders: Mapped[list[Reminder]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[ChatRole] = mapped_column(
        Enum(ChatRole, name="chat_role", values_callable=_values),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="chat_messages")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    schedule_type: Mapped[ScheduleType] = mapped_column(
        Enum(ScheduleType, name="schedule_type", values_callable=_values),
        nullable=False,
    )
    # Recurring rule: 'daily' | 'weekdays' | 'weekly:mon,wed,fri' | NULL for one-off
    recurrence_rule: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # HH:MM IST for recurring fires
    fire_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    # UTC absolute time for one-off fires
    one_off_datetime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[ReminderStatus] = mapped_column(
        Enum(ReminderStatus, name="reminder_status", values_callable=_values),
        nullable=False,
        default=ReminderStatus.active,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="reminders")
    fires: Mapped[list[ReminderFire]] = relationship(
        back_populates="reminder", cascade="all, delete-orphan"
    )


class ReminderFire(Base):
    __tablename__ = "reminder_fires"

    id: Mapped[int] = mapped_column(primary_key=True)
    reminder_id: Mapped[int] = mapped_column(
        ForeignKey("reminders.id", ondelete="CASCADE"), index=True, nullable=False
    )
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome: Mapped[FireOutcome] = mapped_column(
        Enum(FireOutcome, name="fire_outcome", values_callable=_values),
        nullable=False,
        default=FireOutcome.pending,
    )
    snooze_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Telegram + persona impl details
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    persona_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    nag_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_nag_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    reminder: Mapped[Reminder] = relationship(back_populates="fires")


class DailyRecap(Base):
    __tablename__ = "daily_recaps"

    # Primary key on date — IST calendar date the recap covers
    recap_date: Mapped[date] = mapped_column("date", Date, primary_key=True)
    done_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancelled_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pending_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sleep_state: Mapped[SleepState] = mapped_column(
        Enum(SleepState, name="sleep_state", values_callable=_values),
        nullable=False,
        default=SleepState.unknown,
    )
    recap_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MemorySummary(Base):
    __tablename__ = "memory_summary"

    id: Mapped[int] = mapped_column(primary_key=True)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    covers_until: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
