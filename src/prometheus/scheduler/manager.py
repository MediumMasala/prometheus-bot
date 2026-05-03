"""APScheduler instance + helpers. AsyncIOScheduler with SQLAlchemyJobStore (Postgres)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from prometheus.config import settings
from prometheus.utils.logging import log
from prometheus.utils.time import IST, now_utc

if TYPE_CHECKING:
    from telegram.ext import Application

    from prometheus.db.models import Reminder


NAG_INTERVAL_SECONDS = 10 * 60
SNOOZE_MINUTES = 30
SNOOZE_CAP = 3
MAX_NAGS = 3


_app: Application | None = None
_scheduler: AsyncIOScheduler | None = None


def init_scheduler(app: Application) -> AsyncIOScheduler:
    global _app, _scheduler
    _app = app
    jobstore = SQLAlchemyJobStore(url=settings.sync_database_url)
    sch = AsyncIOScheduler(
        jobstores={"default": jobstore},
        timezone=IST,
        job_defaults={
            "coalesce": True,
            "misfire_grace_time": 60,
            "max_instances": 1,
        },
    )
    _scheduler = sch
    return sch


def get_app() -> Application:
    if _app is None:
        raise RuntimeError("scheduler/app not initialized")
    return _app


def get_scheduler() -> AsyncIOScheduler:
    if _scheduler is None:
        raise RuntimeError("scheduler not initialized")
    return _scheduler


# ---------- job id helpers ----------


def reminder_job_id(reminder_id: int) -> str:
    return f"reminder:{reminder_id}"


def nag_job_id(fire_id: int) -> str:
    return f"nag:{fire_id}"


def snooze_job_id(fire_id: int) -> str:
    return f"snooze:{fire_id}"


# ---------- schedule a reminder ----------


def ensure_reminder_job(scheduler: AsyncIOScheduler, reminder: Reminder) -> None:
    """Create or refresh the APScheduler job for a reminder."""
    from prometheus.db.models import ScheduleType

    job_id = reminder_job_id(reminder.id)

    if reminder.schedule_type == ScheduleType.recurring:
        if not reminder.fire_time or not reminder.recurrence_rule:
            log.warning("reminder.missing_recurring_fields", reminder_id=reminder.id)
            return
        rule = reminder.recurrence_rule
        # Translate rule -> day_of_week for CronTrigger
        day_of_week = _rule_to_cron_dow(rule)
        if day_of_week is None:
            log.warning("reminder.unknown_rule", reminder_id=reminder.id, rule=rule)
            return
        trigger = CronTrigger(
            hour=reminder.fire_time.hour,
            minute=reminder.fire_time.minute,
            day_of_week=day_of_week,
            timezone=IST,
        )
        scheduler.add_job(
            "prometheus.scheduler.jobs:fire_reminder",
            trigger=trigger,
            args=[reminder.id],
            id=job_id,
            replace_existing=True,
        )
        log.info(
            "scheduler.reminder_armed",
            reminder_id=reminder.id,
            rule=rule,
            time_ist=reminder.fire_time.strftime("%H:%M"),
        )

    elif reminder.schedule_type == ScheduleType.one_off:
        if not reminder.one_off_datetime:
            log.warning("reminder.missing_oneoff_dt", reminder_id=reminder.id)
            return
        run_at = reminder.one_off_datetime
        if run_at <= now_utc():
            # Slightly past — fire in 5s if within 5 min, else mark missed via job
            if (now_utc() - run_at) <= timedelta(minutes=5):
                run_at = now_utc() + timedelta(seconds=5)
                log.info(
                    "scheduler.oneoff_late_firing",
                    reminder_id=reminder.id,
                    delta_s=(now_utc() - reminder.one_off_datetime).total_seconds(),
                )
            else:
                log.info(
                    "scheduler.oneoff_too_late",
                    reminder_id=reminder.id,
                )
                return
        scheduler.add_job(
            "prometheus.scheduler.jobs:fire_reminder",
            trigger=DateTrigger(run_date=run_at),
            args=[reminder.id],
            id=job_id,
            replace_existing=True,
        )
        log.info(
            "scheduler.oneoff_armed",
            reminder_id=reminder.id,
            run_at_utc=run_at.isoformat(),
        )


def remove_reminder_job(scheduler: AsyncIOScheduler, reminder_id: int) -> None:
    job_id = reminder_job_id(reminder_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


# ---------- nag scheduling ----------


def schedule_nag(
    scheduler: AsyncIOScheduler,
    *,
    fire_id: int,
    delay_seconds: int = NAG_INTERVAL_SECONDS,
) -> None:
    when = now_utc() + timedelta(seconds=delay_seconds)
    scheduler.add_job(
        "prometheus.scheduler.jobs:nag",
        trigger=DateTrigger(run_date=when),
        args=[fire_id],
        id=nag_job_id(fire_id),
        replace_existing=True,
    )


def cancel_nag(scheduler: AsyncIOScheduler, fire_id: int) -> None:
    jid = nag_job_id(fire_id)
    if scheduler.get_job(jid):
        scheduler.remove_job(jid)


def schedule_snooze_fire(
    scheduler: AsyncIOScheduler,
    *,
    reminder_id: int,
    fire_id: int,
    snooze_count: int = 0,
    delay_minutes: int = SNOOZE_MINUTES,
) -> datetime:
    when = now_utc() + timedelta(minutes=delay_minutes)
    scheduler.add_job(
        "prometheus.scheduler.jobs:fire_reminder",
        trigger=DateTrigger(run_date=when),
        args=[reminder_id],
        kwargs={"snooze_count_override": snooze_count},
        id=snooze_job_id(fire_id),
        replace_existing=True,
    )
    return when


# ---------- recurring system jobs ----------


def schedule_system_jobs(scheduler: AsyncIOScheduler) -> None:
    scheduler.add_job(
        "prometheus.scheduler.jobs:midnight_recap",
        trigger=CronTrigger(hour=0, minute=0, timezone=IST),
        id="system:midnight_recap",
        replace_existing=True,
    )
    scheduler.add_job(
        "prometheus.scheduler.jobs:morning_brief",
        trigger=CronTrigger(hour=10, minute=30, timezone=IST),
        id="system:morning_brief",
        replace_existing=True,
    )
    scheduler.add_job(
        "prometheus.scheduler.jobs:sleep_reprompt",
        trigger=CronTrigger(hour=1, minute=0, timezone=IST),
        id="system:sleep_reprompt",
        replace_existing=True,
    )
    scheduler.add_job(
        "prometheus.scheduler.jobs:weekly_memory_regen",
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=IST),
        id="system:weekly_memory_regen",
        replace_existing=True,
    )
    log.info("scheduler.system_jobs_armed")


# ---------- helpers ----------


def _rule_to_cron_dow(rule: str) -> str | None:
    rule = rule.strip().lower()
    if rule == "daily":
        return "*"
    if rule == "weekdays":
        return "mon-fri"
    if rule in ("weekend", "weekends"):
        return "sat,sun"
    if rule.startswith("weekly:"):
        days = [d.strip() for d in rule.removeprefix("weekly:").split(",") if d.strip()]
        valid = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        if all(d in valid for d in days) and days:
            return ",".join(days)
    return None
