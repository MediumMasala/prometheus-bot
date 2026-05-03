from __future__ import annotations

import asyncio
import signal

import tornado.httpserver
from sqlalchemy import text
from telegram.ext import Application, ApplicationBuilder

from prometheus.bot.handlers import register_handlers
from prometheus.config import settings
from prometheus.db.session import engine, session_scope
from prometheus.scheduler.jobs import recover_state
from prometheus.scheduler.manager import (
    init_scheduler,
    schedule_system_jobs,
)
from prometheus.server.web import build_web_app
from prometheus.utils.logging import configure_logging, log

# ----------------- shared post_init / post_shutdown -----------------


async def _post_init_dev(app: Application) -> None:
    """Polling mode boot. Starts scheduler in the same event loop as PTB."""
    async with session_scope() as session:
        await session.execute(text("SELECT 1"))
    scheduler = init_scheduler(app)
    scheduler.start()
    schedule_system_jobs(scheduler)
    await recover_state()
    log.info(
        "ready",
        env=settings.env,
        owner_locked=settings.owner_telegram_user_id is not None,
        mode="polling",
    )


async def _post_shutdown_dev(app: Application) -> None:
    from prometheus.scheduler.manager import get_scheduler

    try:
        sch = get_scheduler()
        if sch.running:
            sch.shutdown(wait=False)
    except RuntimeError:
        pass
    await engine.dispose()
    log.info("shutdown.done", mode="polling")


# ----------------- entrypoint -----------------


def cli() -> None:
    configure_logging()
    if settings.is_prod:
        asyncio.run(_amain_prod())
    else:
        _run_polling()


def _run_polling() -> None:
    log.info("boot", env=settings.env, mode="polling")
    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init_dev)
        .post_shutdown(_post_shutdown_dev)
        .build()
    )
    register_handlers(app)
    app.run_polling(drop_pending_updates=False)


async def _amain_prod() -> None:
    log.info("boot", env=settings.env, mode="webhook")

    if not settings.webhook_url:
        log.error("webhook_url_required_in_prod")
        raise SystemExit(1)

    secret_path = settings.webhook_secret or "default"
    webhook_route = f"/webhook/{secret_path}"
    full_webhook_url = settings.webhook_url.rstrip("/") + webhook_route

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .updater(None)  # we feed updates manually via the tornado handler
        .build()
    )
    register_handlers(app)

    await app.initialize()

    scheduler = init_scheduler(app)
    scheduler.start()
    schedule_system_jobs(scheduler)
    await recover_state()

    async with session_scope() as session:
        await session.execute(text("SELECT 1"))

    await app.start()  # processes updates from update_queue

    secret_token = settings.webhook_secret or None
    await app.bot.set_webhook(
        url=full_webhook_url,
        secret_token=secret_token,
        drop_pending_updates=False,
    )
    log.info("webhook.registered", url=full_webhook_url)

    web_app = build_web_app(
        application=app,
        scheduler=scheduler,
        webhook_path=webhook_route,
        webhook_secret=settings.webhook_secret,
    )
    server = tornado.httpserver.HTTPServer(web_app)
    server.listen(settings.port, address="0.0.0.0")
    log.info("ready", port=settings.port, mode="webhook")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        log.info("signal.received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    finally:
        log.info("shutdown.start")
        try:
            await app.bot.delete_webhook()
        except Exception as exc:  # noqa: BLE001
            log.warning("delete_webhook.failed", error=str(exc))
        try:
            scheduler.shutdown(wait=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler.shutdown_failed", error=str(exc))
        try:
            await app.stop()
            await app.shutdown()
        except Exception as exc:  # noqa: BLE001
            log.warning("ptb.shutdown_failed", error=str(exc))
        server.stop()
        await engine.dispose()
        log.info("shutdown.done", mode="webhook")


if __name__ == "__main__":
    cli()
