"""Tornado HTTP server for production webhook + /healthz.

PTB v22 owns its own webhook server, but it doesn't expose a way to add a
/healthz route. So in prod we run our own tornado app, accept Telegram
webhook POSTs at /webhook/<secret_path>, queue updates into PTB's
update_queue, and answer /healthz independently.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import tornado.web
from sqlalchemy import text
from telegram import Update

from prometheus.db.session import session_scope
from prometheus.utils.logging import log

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from telegram.ext import Application


class WebhookHandler(tornado.web.RequestHandler):
    # Note: tornado's RequestHandler already uses `self.application` for the
    # tornado web app. We deliberately use a different name here.
    bot_app: Application
    expected_secret: str

    def initialize(self, *, bot_app: Application, expected_secret: str) -> None:
        self.bot_app = bot_app
        self.expected_secret = expected_secret

    async def post(self) -> None:
        got = self.request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if self.expected_secret and got != self.expected_secret:
            log.warning("webhook.bad_secret")
            self.set_status(403)
            return
        try:
            payload = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            log.warning("webhook.bad_json")
            self.set_status(400)
            return
        upd = Update.de_json(payload, self.bot_app.bot)
        await self.bot_app.update_queue.put(upd)
        self.set_status(200)
        self.write({"ok": True})

    def check_xsrf_cookie(self) -> None:  # noqa: D401 — disable XSRF for webhook
        return None


class HealthzHandler(tornado.web.RequestHandler):
    scheduler: AsyncIOScheduler

    def initialize(self, *, scheduler: AsyncIOScheduler) -> None:
        self.scheduler = scheduler

    async def get(self) -> None:
        if not self.scheduler.running:
            self.set_status(503)
            self.write({"status": "scheduler_down"})
            return
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            log.warning("healthz.db_failed", error=str(exc))
            self.set_status(503)
            self.write({"status": "db_down"})
            return
        self.set_status(200)
        self.write({"status": "ok"})


class RootHandler(tornado.web.RequestHandler):
    async def get(self) -> None:
        self.set_status(200)
        self.write({"name": "prometheus", "status": "alive"})


def build_web_app(
    *,
    application: Application,
    scheduler: AsyncIOScheduler,
    webhook_path: str,
    webhook_secret: str,
) -> tornado.web.Application:
    return tornado.web.Application(
        [
            (r"/", RootHandler),
            (r"/healthz", HealthzHandler, {"scheduler": scheduler}),
            (
                webhook_path,
                WebhookHandler,
                {
                    "bot_app": application,
                    "expected_secret": webhook_secret,
                },
            ),
        ]
    )
