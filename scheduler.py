"""APScheduler setup and job execution logic.

Includes rate limiting, blacklisting, inline buttons, parse mode, and silent messages.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from config import DATABASE_URL, rewrite_db_url_sync

logger = logging.getLogger(__name__)

scheduler: AsyncIOScheduler | None = None

# ── Rate limiter: per-chat send throttle ─────────────────────────────────────

_last_send: dict[int, float] = defaultdict(float)
_MIN_SEND_INTERVAL = 1.0  # seconds between sends to the same chat


def _rate_limit_sync(chat_id: int) -> None:
    """Synchronous rate limit — blocks the event loop briefly. Use _rate_limit for async."""
    now = time.monotonic()
    elapsed = now - _last_send[chat_id]
    if elapsed < _MIN_SEND_INTERVAL:
        time.sleep(_MIN_SEND_INTERVAL - elapsed)
    _last_send[chat_id] = time.monotonic()


async def _rate_limit(chat_id: int) -> None:
    """Non-blocking rate limit using asyncio.sleep."""
    now = time.monotonic()
    elapsed = now - _last_send[chat_id]
    if elapsed < _MIN_SEND_INTERVAL:
        await asyncio.sleep(_MIN_SEND_INTERVAL - elapsed)
    _last_send[chat_id] = time.monotonic()


def setup_scheduler(application) -> AsyncIOScheduler:
    global scheduler

    jobstores = {
        "default": SQLAlchemyJobStore(
            url=rewrite_db_url_sync(DATABASE_URL),
            tablename="apscheduler_jobs",
        )
    }

    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 3600,
        },
    )

    scheduler.ctx = {"application": application}
    return scheduler


def _build_buttons(buttons_json: str | None) -> InlineKeyboardMarkup | None:
    """Parse buttons_json into an InlineKeyboardMarkup.

    Expected format: JSON array of arrays of button objects.
    Each button: {"text": "...", "url": "..."} or {"text": "...", "callback_data": "..."}
    """
    if not buttons_json:
        return None
    try:
        rows = json.loads(buttons_json)
        keyboard = []
        for row in rows:
            keyboard.append([
                InlineKeyboardButton(
                    btn["text"],
                    url=btn.get("url"),
                    callback_data=btn.get("callback_data"),
                )
                for btn in row
            ])
        return InlineKeyboardMarkup(keyboard) if keyboard else None
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


async def _is_blacklisted(conn, chat_id: int) -> bool:
    """Check if a chat is blacklisted."""
    row = await conn.fetchrow("SELECT id FROM blacklist WHERE chat_id = $1", chat_id)
    return row is not None


async def send_scheduled_message(schedule_id: int) -> None:
    """Execute a scheduled message — called by APScheduler."""
    from db import get_pool

    application = scheduler.ctx["application"]
    bot = application.bot
    pool = get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM schedules WHERE id = $1", schedule_id
        )

    if not row:
        logger.warning(f"Schedule {schedule_id} not found, removing job")
        remove_job(schedule_id)
        return

    if not row["is_active"] or row["is_paused"]:
        logger.info(f"Schedule {schedule_id} is inactive/paused, skipping")
        return

    chat_id = row["target_chat_id"]

    # Blacklist check
    async with pool.acquire() as conn:
        if await _is_blacklisted(conn, chat_id):
            logger.warning(f"Schedule {schedule_id}: target {chat_id} is blacklisted, skipping")
            await conn.execute(
                """INSERT INTO send_log (schedule_id, target_chat_id, status, error_message)
                   VALUES ($1, $2, 'failed', 'Target is blacklisted')""",
                schedule_id, chat_id,
            )
            return

    # Rate limit
    await _rate_limit(chat_id)

    message_type = row["message_type"]
    content_text = row["content_text"]
    file_id = row["file_id"]
    schedule_type = row["schedule_type"]
    parse_mode = row["parse_mode"]
    silent = row["silent"]
    buttons = _build_buttons(row["buttons_json"])

    sent = False
    error_msg = None
    sent_message_id = None

    try:
        kwargs: dict = {"chat_id": chat_id}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        if silent:
            kwargs["disable_notification"] = True

        if message_type == "photo" and file_id:
            kwargs["photo"] = file_id
            kwargs["caption"] = content_text or ""
            if buttons:
                kwargs["reply_markup"] = buttons
            msg = await bot.send_photo(**kwargs)
            sent_message_id = msg.message_id
            sent = True
        elif message_type == "document" and file_id:
            kwargs["document"] = file_id
            kwargs["caption"] = content_text or ""
            if buttons:
                kwargs["reply_markup"] = buttons
            msg = await bot.send_document(**kwargs)
            sent_message_id = msg.message_id
            sent = True
        else:
            if content_text:
                kwargs["text"] = content_text
                if buttons:
                    kwargs["reply_markup"] = buttons
                msg = await bot.send_message(**kwargs)
                sent_message_id = msg.message_id
                sent = True
            else:
                logger.warning(f"Schedule {schedule_id}: no content to send")
                error_msg = "Empty content"

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Send failed for schedule {schedule_id}: {error_msg}")

    # Log the result
    status = "success" if sent else "failed"
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO send_log (schedule_id, target_chat_id, status, error_message, message_id)
               VALUES ($1, $2, $3, $4, $5)""",
            schedule_id, chat_id, status, error_msg, sent_message_id,
        )
        now = datetime.now(timezone.utc)
        await conn.execute(
            "UPDATE schedules SET last_run_at = $1 WHERE id = $2", now, schedule_id,
        )

    # For one-time schedules, deactivate after sending
    if schedule_type == "once" and sent:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE schedules SET is_active = FALSE WHERE id = $1", schedule_id,
            )
        remove_job(schedule_id)
        logger.info(f"Schedule {schedule_id} completed (one-time)")

    # Update next_run_at for recurring schedules
    if schedule_type in ("daily", "weekly", "interval", "cron") and sent:
        job = scheduler.get_job(f"schedule_{schedule_id}")
        next_run = job.next_run_time if job else None
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE schedules SET next_run_at = $1 WHERE id = $2",
                next_run, schedule_id,
            )


def add_job(schedule_id: int, schedule_row: dict) -> None:
    """Add or update an APScheduler job from a schedule row."""
    global scheduler
    if not scheduler:
        return

    job_id = f"schedule_{schedule_id}"
    schedule_type = schedule_row["schedule_type"]
    tz_name = schedule_row.get("timezone", "Asia/Kolkata")

    try:
        tz = pytz.timezone(tz_name)
    except pytz.exceptions.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone {tz_name}, using UTC")
        tz = pytz.UTC

    trigger = None

    if schedule_type == "once":
        run_at = schedule_row.get("run_at")
        if run_at:
            if run_at.tzinfo is None:
                run_at = tz.localize(run_at)
            trigger = DateTrigger(run_date=run_at)

    elif schedule_type == "daily":
        run_at = schedule_row.get("run_at")
        hour, minute = 9, 0
        if run_at:
            hour, minute = run_at.hour, run_at.minute
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)

    elif schedule_type == "weekly":
        run_at = schedule_row.get("run_at")
        hour, minute = 9, 0
        dow = "mon"
        if run_at:
            hour, minute = run_at.hour, run_at.minute
            dow_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
            dow = dow_map.get(run_at.weekday(), "mon")
        trigger = CronTrigger(day_of_week=dow, hour=hour, minute=minute, timezone=tz)

    elif schedule_type == "interval":
        cron_expr = schedule_row.get("cron_expression", "*/30 * * * *")
        trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)

    elif schedule_type == "cron":
        cron_expr = schedule_row.get("cron_expression")
        if cron_expr:
            trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)

    if trigger:
        try:
            scheduler.add_job(
                send_scheduled_message,
                trigger=trigger,
                id=job_id,
                args=[schedule_id],
                replace_existing=True,
            )
            logger.info(f"Job {job_id} scheduled ({schedule_type})")
        except Exception as e:
            logger.error(f"Failed to schedule job {job_id}: {e}")


def remove_job(schedule_id: int) -> None:
    """Remove an APScheduler job."""
    global scheduler
    if not scheduler:
        return
    job_id = f"schedule_{schedule_id}"
    try:
        scheduler.remove_job(job_id)
        logger.info(f"Removed job {job_id}")
    except Exception:
        pass
