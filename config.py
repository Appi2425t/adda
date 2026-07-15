"""Telegram Auto-Scheduler Bot — Configuration.

Loads environment variables and provides validation + URL rewriting helpers.
"""

import os
import sys

BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
ADMIN_IDS_RAW: str = os.environ.get("ADMIN_IDS", "")
DEFAULT_TIMEZONE: str = os.environ.get("DEFAULT_TIMEZONE", "Asia/Kolkata")
PORT: int = int(os.environ.get("PORT", "8080"))

# Parse admin IDs into a set for O(1) lookup
ADMIN_IDS: set[int] = set()
if ADMIN_IDS_RAW:
    ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()}


def rewrite_db_url(url: str) -> str:
    """Rewrite postgres:// or postgresql:// to postgresql+asyncpg:// for asyncpg."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def rewrite_db_url_sync(url: str) -> str:
    """Ensure the URL uses postgresql:// (sync SQLAlchemy for APScheduler job store)."""
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def validate() -> None:
    """Fail fast if required env vars are missing."""
    errors: list[str] = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN is not set")
    if not DATABASE_URL:
        errors.append(
            "DATABASE_URL is not set — attach the Postgres plugin in Railway"
        )
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
