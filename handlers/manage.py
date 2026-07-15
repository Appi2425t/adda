"""Schedule management: list, cancel, pause, resume, preview, test, export, import, logs, next runs, duplicates."""

import json
import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO

from telegram import Update
from telegram.ext import ContextTypes

from db import get_pool
from scheduler import remove_job, add_job
from handlers.admin import check_admin

logger = logging.getLogger(__name__)


# ── List ─────────────────────────────────────────────────────────────────────


async def schedules_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all active schedules."""
    if not await check_admin(update):
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, target_chat_id, target_label, message_type,
                      content_text, schedule_type, next_run_at, is_paused, silent
               FROM schedules
               WHERE is_active = TRUE
               ORDER BY next_run_at NULLS LAST"""
        )

    if not rows:
        await update.message.reply_text("No active schedules.")
        return

    lines = ["Active Schedules:\n"]
    for r in rows:
        preview = (r["content_text"][:60] + "...") if r["content_text"] and len(r["content_text"]) > 60 else (r["content_text"] or "(media)")
        status = "PAUSED" if r["is_paused"] else "active"
        silent = " | silent" if r["silent"] else ""
        lines.append(
            f"ID: {r['id']} | {r['schedule_type']} | {status}{silent}\n"
            f"  Target: {r['target_label'] or r['target_chat_id']}\n"
            f"  Next: {r['next_run_at'] or 'N/A'}\n"
            f"  Content: {preview}\n"
        )

    await update.message.reply_text("\n".join(lines))


# ── Cancel ───────────────────────────────────────────────────────────────────


async def cancel_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel a schedule by ID."""
    if not await check_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /cancel <schedule_id>")
        return

    try:
        sid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid schedule ID.")
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM schedules WHERE id = $1 AND is_active = TRUE", sid
        )
        if not row:
            await update.message.reply_text(f"Schedule {sid} not found or already inactive.")
            return
        await conn.execute("UPDATE schedules SET is_active = FALSE WHERE id = $1", sid)

    remove_job(sid)
    await update.message.reply_text(f"Schedule {sid} cancelled.")


# ── Pause ────────────────────────────────────────────────────────────────────


async def pause_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause a schedule."""
    if not await check_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /pause <schedule_id>")
        return

    try:
        sid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid schedule ID.")
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, is_paused FROM schedules WHERE id = $1 AND is_active = TRUE", sid
        )
        if not row:
            await update.message.reply_text(f"Schedule {sid} not found.")
            return
        if row["is_paused"]:
            await update.message.reply_text(f"Schedule {sid} is already paused.")
            return
        await conn.execute("UPDATE schedules SET is_paused = TRUE WHERE id = $1", sid)

    from scheduler import scheduler as sched
    if sched:
        try:
            sched.pause_job(f"schedule_{sid}")
        except Exception:
            pass

    await update.message.reply_text(f"Schedule {sid} paused.")


# ── Resume ───────────────────────────────────────────────────────────────────


async def resume_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume a paused schedule."""
    if not await check_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /resume <schedule_id>")
        return

    try:
        sid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid schedule ID.")
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, is_paused FROM schedules WHERE id = $1 AND is_active = TRUE", sid
        )
        if not row:
            await update.message.reply_text(f"Schedule {sid} not found.")
            return
        if not row["is_paused"]:
            await update.message.reply_text(f"Schedule {sid} is not paused.")
            return
        await conn.execute("UPDATE schedules SET is_paused = FALSE WHERE id = $1", sid)

    from scheduler import scheduler as sched
    if sched:
        try:
            sched.resume_job(f"schedule_{sid}")
        except Exception:
            pass

    await update.message.reply_text(f"Schedule {sid} resumed.")


# ── Preview ──────────────────────────────────────────────────────────────────


async def preview_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show what a schedule will send."""
    if not await check_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /preview <schedule_id>")
        return

    try:
        sid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid schedule ID.")
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM schedules WHERE id = $1", sid)
        if not row:
            await update.message.reply_text(f"Schedule {sid} not found.")
            return

    text = f"Preview of Schedule #{sid}:\n\n"
    text += f"Type: {row['schedule_type']}\n"
    text += f"Target: {row['target_label'] or row['target_chat_id']}\n"
    text += f"Silent: {'Yes' if row['silent'] else 'No'}\n"

    if row["content_text"]:
        text += f"\nMessage:\n{row['content_text']}\n"

    if row["file_id"]:
        text += f"\nMedia attached (file_id: {row['file_id'][:20]}...)\n"

    await update.message.reply_text(text)


# ── Test Send ────────────────────────────────────────────────────────────────


async def test_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a test message to verify the bot can reach the target. /test <schedule_id>"""
    if not await check_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /test <schedule_id>")
        return

    try:
        sid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid schedule ID.")
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM schedules WHERE id = $1", sid)
        if not row:
            await update.message.reply_text(f"Schedule {sid} not found.")
            return

    bot = context.bot
    chat_id = row["target_chat_id"]

    try:
        if row["message_type"] == "photo" and row["file_id"]:
            await bot.send_photo(
                chat_id=chat_id, photo=row["file_id"],
                caption=f"[TEST] {row['content_text'] or ''}",
            )
        elif row["message_type"] == "document" and row["file_id"]:
            await bot.send_document(
                chat_id=chat_id, document=row["file_id"],
                caption=f"[TEST] {row['content_text'] or ''}",
            )
        elif row["content_text"]:
            await bot.send_message(chat_id=chat_id, text=f"[TEST] {row['content_text']}")
        else:
            await update.message.reply_text("Schedule has no content to test.")
            return

        await update.message.reply_text(f"Test message sent to {chat_id} successfully!")
    except Exception as e:
        await update.message.reply_text(f"Test send failed: {e}")


# ── Targets ──────────────────────────────────────────────────────────────────


async def add_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save a target chat with a label. /addtarget <chat_id> <label>"""
    if not await check_admin(update):
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addtarget <chat_id> <label>")
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid chat ID.")
        return

    label = " ".join(context.args[1:])
    user_id = update.effective_user.id

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO targets (chat_id, label, added_by)
               VALUES ($1, $2, $3)
               ON CONFLICT (chat_id, label) DO NOTHING""",
            chat_id, label, user_id,
        )

    await update.message.reply_text(f"Target '{label}' (chat {chat_id}) saved.")


async def remove_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a saved target. /removetarget <label>"""
    if not await check_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /removetarget <label>")
        return

    label = context.args[0]
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM targets WHERE label = $1", label)

    if result == "DELETE 0":
        await update.message.reply_text(f"Target '{label}' not found.")
    else:
        await update.message.reply_text(f"Target '{label}' removed.")


async def list_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all saved targets. /targets"""
    if not await check_admin(update):
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, label, added_by, added_at FROM targets ORDER BY label"
        )

    if not rows:
        await update.message.reply_text("No saved targets.")
        return

    lines = ["Saved Targets:\n"]
    for r in rows:
        lines.append(f"{r['label']} — Chat ID: {r['chat_id']} (added by {r['added_by']})")

    await update.message.reply_text("\n".join(lines))


# ── Export / Import ──────────────────────────────────────────────────────────


async def export_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export all active schedules as JSON. /export"""
    if not await check_admin(update):
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM schedules WHERE is_active = TRUE ORDER BY id"
        )

    if not rows:
        await update.message.reply_text("No active schedules to export.")
        return

    export_data = []
    for r in rows:
        d = dict(r)
        for key in ("created_at", "last_run_at", "next_run_at", "run_at"):
            if d.get(key):
                d[key] = d[key].isoformat()
        export_data.append(d)

    json_str = json.dumps(export_data, indent=2, default=str)

    bio = BytesIO(json_str.encode("utf-8"))
    bio.name = "schedules_export.json"
    await update.message.reply_document(
        document=bio,
        caption=f"Exported {len(export_data)} schedule(s).",
    )


async def import_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Import schedules from a JSON file. /import (reply to a JSON file)"""
    if not await check_admin(update):
        return

    if not update.message.document:
        await update.message.reply_text(
            "Reply to a JSON file with /import to import schedules."
        )
        return

    if not update.message.document.file_name.endswith(".json"):
        await update.message.reply_text("Please send a .json file.")
        return

    file = await context.bot.get_file(update.message.document.file_id)
    content = await file.download_as_bytearray()
    data = json.loads(content.decode("utf-8"))

    if not isinstance(data, list):
        await update.message.reply_text("Invalid format: expected a JSON array.")
        return

    pool = get_pool()
    imported = 0
    errors = 0

    for sched_data in data:
        try:
            run_at = None
            if sched_data.get("run_at"):
                run_at = datetime.fromisoformat(sched_data["run_at"])

            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO schedules
                       (created_by, target_chat_id, target_label, message_type,
                        content_text, file_id, schedule_type, cron_expression,
                        run_at, timezone, parse_mode, silent, buttons_json)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                       RETURNING id""",
                    sched_data.get("created_by", update.effective_user.id),
                    sched_data["target_chat_id"],
                    sched_data.get("target_label"),
                    sched_data.get("message_type", "text"),
                    sched_data.get("content_text"),
                    sched_data.get("file_id"),
                    sched_data.get("schedule_type", "once"),
                    sched_data.get("cron_expression"),
                    run_at,
                    sched_data.get("timezone", "Asia/Kolkata"),
                    sched_data.get("parse_mode"),
                    sched_data.get("silent", False),
                    sched_data.get("buttons_json"),
                )
            add_job(row["id"], dict(sched_data))
            imported += 1
        except Exception as e:
            logger.error(f"Import error: {e}")
            errors += 1

    await update.message.reply_text(
        f"Import complete!\nImported: {imported}\nErrors: {errors}"
    )


# ── Next Runs ────────────────────────────────────────────────────────────────


async def next_runs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the next 10 upcoming scheduled sends. /next"""
    if not await check_admin(update):
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, target_chat_id, target_label, schedule_type, next_run_at, content_text
               FROM schedules
               WHERE is_active = TRUE AND is_paused = FALSE AND next_run_at IS NOT NULL
               ORDER BY next_run_at
               LIMIT 10"""
        )

    if not rows:
        await update.message.reply_text("No upcoming scheduled sends.")
        return

    lines = ["Next 10 upcoming sends:\n"]
    for i, r in enumerate(rows, 1):
        preview = (r["content_text"][:30] + "...") if r["content_text"] and len(r["content_text"]) > 30 else (r["content_text"] or "(media)")
        lines.append(
            f"{i}. #{r['id']} | {r['next_run_at']:%Y-%m-%d %H:%M UTC}\n"
            f"   {r['schedule_type']} → {r['target_label'] or r['target_chat_id']}\n"
            f"   {preview}\n"
        )

    await update.message.reply_text("\n".join(lines))


# ── Send Logs ────────────────────────────────────────────────────────────────


async def send_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent send history. /logs [hours]"""
    if not await check_admin(update):
        return

    hours = 24
    if context.args:
        try:
            hours = int(context.args[0])
            hours = max(1, min(hours, 168))  # 1 hour to 7 days
        except ValueError:
            pass

    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT sl.id, sl.schedule_id, sl.target_chat_id, sl.status,
                      sl.error_message, sl.message_id, sl.sent_at
               FROM send_log sl
               WHERE sl.sent_at > $1
               ORDER BY sl.sent_at DESC
               LIMIT 20""",
            since,
        )

    if not rows:
        await update.message.reply_text(f"No send logs in the last {hours}h.")
        return

    lines = [f"Send logs (last {hours}h):\n"]
    for r in rows:
        icon = "OK" if r["status"] == "success" else "FAIL"
        sched = f"Schedule #{r['schedule_id']}" if r["schedule_id"] else "broadcast"
        error = f" — {r['error_message']}" if r["error_message"] else ""
        lines.append(
            f"[{icon}] {r['sent_at']:%m-%d %H:%M} | {sched} → {r['target_chat_id']}{error}"
        )

    await update.message.reply_text("\n".join(lines))


# ── Duplicate Detection ──────────────────────────────────────────────────────


async def check_duplicate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check for duplicate schedules. /duplicates"""
    if not await check_admin(update):
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT target_chat_id, content_text, COUNT(*) as cnt
               FROM schedules
               WHERE is_active = TRUE
               GROUP BY target_chat_id, content_text
               HAVING COUNT(*) > 1"""
        )

    if not rows:
        await update.message.reply_text("No duplicate schedules found.")
        return

    lines = ["Potential duplicates:\n"]
    for r in rows:
        preview = (r["content_text"][:40] + "...") if r["content_text"] and len(r["content_text"]) > 40 else (r["content_text"] or "(media)")
        lines.append(f"Chat {r['target_chat_id']} — {r['cnt']}x — {preview}")

    await update.message.reply_text("\n".join(lines))
