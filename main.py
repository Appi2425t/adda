"""Telegram Auto-Scheduler Bot — Entry point.

Startup sequence: load config → validate → connect Postgres → run migrations
→ hydrate scheduler from DB → start polling.
"""

import asyncio
import logging
import signal
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

import config
import db
from scheduler import setup_scheduler, add_job
from handlers.schedule import get_conversation_handler
from handlers.broadcast import get_broadcast_conversation
from handlers.edit import get_edit_conversation
from handlers.templates import (
    get_template_conversation,
    template_list,
    template_use,
    template_delete,
)
from handlers.manage import (
    schedules_list,
    cancel_schedule,
    pause_schedule,
    resume_schedule,
    preview_schedule,
    test_send,
    add_target,
    remove_target,
    list_targets,
    export_schedules,
    import_schedules,
    next_runs,
    send_logs,
    check_duplicate,
)
from handlers.blacklist import blacklist_add, blacklist_remove, blacklist_list
from handlers.admin import whoami
from handlers.misc import start, help_cmd, stats, cleanup

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── Graceful shutdown ────────────────────────────────────────────────────────

shutdown_event = asyncio.Event()


def signal_handler(sig, frame):
    logger.info(f"Received {signal.Signals(sig).name}, shutting down...")
    shutdown_event.set()


# ── Error handler ────────────────────────────────────────────────────────────


async def error_handler(update: object, context) -> None:
    """Global error handler — log and don't crash."""
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "An error occurred while processing your request. Please try again."
            )
        except Exception:
            pass


# ── Post-init: hydrate scheduler from DB ────────────────────────────────────


async def post_init(application: Application) -> None:
    """Connect DB, run migrations, load all active schedules into APScheduler."""
    await db.connect()
    await db.run_migrations()

    sched = setup_scheduler(application)
    sched.start()

    pool = db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM schedules WHERE is_active = TRUE")

    loaded = 0
    for row in rows:
        row_dict = dict(row)
        add_job(row_dict["id"], row_dict)
        if not row_dict["is_paused"]:
            loaded += 1

    logger.info(f"Loaded {loaded} active schedules into scheduler")
    logger.info("Bot started and ready!")


# ── Post-shutdown: close DB ─────────────────────────────────────────────────


async def post_shutdown(application: Application) -> None:
    """Clean up on shutdown."""
    from scheduler import scheduler as sched

    if sched and sched.running:
        sched.shutdown(wait=False)
    await db.close()
    logger.info("Bot shutdown complete")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    config.validate()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ── Conversation handlers (must be added first) ─────────────────────────
    application.add_handler(get_conversation_handler())
    application.add_handler(get_broadcast_conversation())
    application.add_handler(get_edit_conversation())
    application.add_handler(get_template_conversation())

    # ── Core commands ───────────────────────────────────────────────────────
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("cleanup", cleanup))

    # ── Schedule management ─────────────────────────────────────────────────
    application.add_handler(CommandHandler("schedules", schedules_list))
    application.add_handler(CommandHandler("mylist", schedules_list))
    application.add_handler(CommandHandler("cancel", cancel_schedule))
    application.add_handler(CommandHandler("pause", pause_schedule))
    application.add_handler(CommandHandler("resume", resume_schedule))
    application.add_handler(CommandHandler("preview", preview_schedule))
    application.add_handler(CommandHandler("test", test_send))
    application.add_handler(CommandHandler("next", next_runs))
    application.add_handler(CommandHandler("logs", send_logs))
    application.add_handler(CommandHandler("duplicates", check_duplicate))

    # ── Targets ─────────────────────────────────────────────────────────────
    application.add_handler(CommandHandler("addtarget", add_target))
    application.add_handler(CommandHandler("removetarget", remove_target))
    application.add_handler(CommandHandler("targets", list_targets))

    # ── Templates (standalone commands) ─────────────────────────────────────
    application.add_handler(CommandHandler("templates", template_list))
    application.add_handler(CommandHandler("usetemplate", template_use))
    application.add_handler(CommandHandler("deletetemplate", template_delete))

    # ── Blacklist ───────────────────────────────────────────────────────────
    application.add_handler(CommandHandler("blacklistadd", blacklist_add))
    application.add_handler(CommandHandler("unblacklist", blacklist_remove))
    application.add_handler(CommandHandler("blacklist", blacklist_list))

    # ── Export / Import ─────────────────────────────────────────────────────
    application.add_handler(CommandHandler("export", export_schedules))
    application.add_handler(CommandHandler("import", import_schedules))

    # Handle import via file reply
    async def import_reply_handler(update: Update, context):
        if update.message.document:
            await import_schedules(update, context)

    application.add_handler(MessageHandler(
        filters.Document.ALL & filters.Regex(r"\.json$"),
        import_reply_handler,
    ))

    # ── Global error handler ────────────────────────────────────────────────
    application.add_error_handler(error_handler)

    logger.info("Starting bot in polling mode...")
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
