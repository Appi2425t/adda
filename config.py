"""Telegram Auto-Scheduler Bot — Configuration.

Auto-detects Railway environment variables including hash-suffixed DATABASE_URL.
Provides validation + URL rewriting helpers.
"""

import os
import re
import sys
import logging

logger = logging.getLogger(__name__)

# ── Auto-detect DATABASE_URL (Railway appends hash suffix) ──────────────────


def _find_database_url() -> str:
    """Scan env vars for DATABASE_URL, including Railway's hash-suffixed variants."""
    # 1. Exact match first
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url

    # 2. Railway suffixes: DATABASE_URL_PG, DATABASE_URL_XYZ123, etc.
    pattern = re.compile(r"^DATABASE_URL[_A-Z0-9]*$", re.IGNORECASE)
    for key, val in os.environ.items():
        if pattern.match(key) and val:
            logger.info(f"Found database URL in env var: {key}")
            return val

    return ""


def _find_bot_token() -> str:
    """Scan env vars for BOT_TOKEN, including Railway's hash-suffixed variants."""
    token = os.environ.get("BOT_TOKEN", "")
    if token:
        return token

    pattern = re.compile(r"^BOT_TOKEN[_A-Z0-9]*$", re.IGNORECASE)
    for key, val in os.environ.items():
        if pattern.match(key) and val:
            logger.info(f"Found bot token in env var: {key}")
            return val

    return ""


def _find_admin_ids() -> str:
    """Scan env vars for ADMIN_IDS, including Railway's hash-suffixed variants."""
    ids = os.environ.get("ADMIN_IDS", "")
    if ids:
        return ids

    pattern = re.compile(r"^ADMIN_IDS[_A-Z0-9]*$", re.IGNORECASE)
    for key, val in os.environ.items():
        if pattern.match(key) and val:
            logger.info(f"Found admin IDs in env var: {key}")
            return val

    return ""


# ── Load variables ──────────────────────────────────────────────────────────

BOT_TOKEN: str = _find_bot_token()
DATABASE_URL: str = _find_database_url()
ADMIN_IDS_RAW: str = _find_admin_ids()
DEFAULT_TIMEZONE: str = os.environ.get("DEFAULT_TIMEZONE", "Asia/Kolkata")
PORT: int = int(os.environ.get("PORT", "8080"))

# Parse admin IDs into a set for O(1) lookup
ADMIN_IDS: set[int] = set()
if ADMIN_IDS_RAW:
    try:
        ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()}
    except ValueError as e:
        logger.warning(f"Invalid ADMIN_IDS format: {ADMIN_IDS_RAW} ({e})")


# ── URL rewriting ──────────────────────────────────────────────────────────


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


# ── Validation ─────────────────────────────────────────────────────────────


def validate() -> None:
    """Fail fast if required env vars are missing. Logs what was detected."""
    print("=" * 60, file=sys.stderr)
    print("Environment Variable Detection", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    errors: list[str] = []

    # BOT_TOKEN
    if BOT_TOKEN:
        masked = BOT_TOKEN[:8] + "..." + BOT_TOKEN[-4:] if len(BOT_TOKEN) > 12 else "***"
        print(f"  BOT_TOKEN:     detected ({masked})", file=sys.stderr)
    else:
        print("  BOT_TOKEN:     NOT FOUND", file=sys.stderr)
        errors.append("BOT_TOKEN is not set")

    # DATABASE_URL
    if DATABASE_URL:
        # Mask password in URL for logging
        masked_url = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", DATABASE_URL)
        print(f"  DATABASE_URL:  detected ({masked_url})", file=sys.stderr)
    else:
        print("  DATABASE_URL:  NOT FOUND", file=sys.stderr)
        errors.append(
            "DATABASE_URL is not set — attach the Postgres plugin in Railway"
        )

    # ADMIN_IDS
    if ADMIN_IDS:
        print(f"  ADMIN_IDS:     detected ({len(ADMIN_IDS)} admin(s))", file=sys.stderr)
    else:
        print("  ADMIN_IDS:     NOT SET (anyone can manage schedules)", file=sys.stderr)

    # DEFAULT_TIMEZONE
    print(f"  DEFAULT_TZ:    {DEFAULT_TIMEZONE}", file=sys.stderr)

    # PORT
    print(f"  PORT:          {PORT}", file=sys.stderr)

    # Scan for other Railway-injected vars
    railway_vars = [k for k in os.environ if k.startswith("RAILWAY_") or k.startswith("PORT")]
    if railway_vars:
        print(f"\n  Railway vars detected: {', '.join(railway_vars)}", file=sys.stderr)

    print("=" * 60, file=sys.stderr)

    if errors:
        print("\nFATAL ERRORS:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "\nOn Railway:\n"
            "  1. Attach the PostgreSQL plugin (Database → PostgreSQL)\n"
            "  2. Set BOT_TOKEN in the Variables tab\n"
            "  3. Set ADMIN_IDS (your Telegram user ID) in the Variables tab\n"
            "  4. DATABASE_URL is auto-injected by the Postgres plugin",
            file=sys.stderr,
        )
        sys.exit(1)
