"""Telegram Auto-Scheduler Bot — Database layer.

Asyncpg connection pool, idempotent migrations, and helpers.
"""

import logging

import asyncpg

from config import DATABASE_URL, rewrite_db_url

logger = logging.getLogger(__name__)

pool: asyncpg.Pool | None = None

MIGRATIONS = """
-- Schedules table
CREATE TABLE IF NOT EXISTS schedules (
    id SERIAL PRIMARY KEY,
    created_by BIGINT NOT NULL,
    target_chat_id BIGINT NOT NULL,
    target_label TEXT,
    message_type TEXT NOT NULL DEFAULT 'text',
    content_text TEXT,
    file_id TEXT,
    schedule_type TEXT NOT NULL DEFAULT 'once',
    cron_expression TEXT,
    run_at TIMESTAMPTZ,
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_paused BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    parse_mode TEXT DEFAULT NULL,
    silent BOOLEAN NOT NULL DEFAULT FALSE,
    buttons_json TEXT DEFAULT NULL
);

-- Targets table
CREATE TABLE IF NOT EXISTS targets (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    label TEXT NOT NULL,
    added_by BIGINT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chat_id, label)
);

-- Send log table
CREATE TABLE IF NOT EXISTS send_log (
    id SERIAL PRIMARY KEY,
    schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL,
    target_chat_id BIGINT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    message_id BIGINT,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Blacklist table
CREATE TABLE IF NOT EXISTS blacklist (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL UNIQUE,
    reason TEXT,
    added_by BIGINT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Templates table
CREATE TABLE IF NOT EXISTS templates (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    content_text TEXT,
    message_type TEXT NOT NULL DEFAULT 'text',
    file_id TEXT,
    parse_mode TEXT,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_schedules_active ON schedules(is_active, is_paused);
CREATE INDEX IF NOT EXISTS idx_schedules_next_run ON schedules(next_run_at) WHERE is_active = TRUE AND is_paused = FALSE;
CREATE INDEX IF NOT EXISTS idx_send_log_schedule ON send_log(schedule_id);
CREATE INDEX IF NOT EXISTS idx_send_log_sent_at ON send_log(sent_at);
CREATE INDEX IF NOT EXISTS idx_blacklist_chat ON blacklist(chat_id);
"""


async def connect() -> asyncpg.Pool:
    global pool
    url = rewrite_db_url(DATABASE_URL)
    pool = await asyncpg.create_pool(url, min_size=2, max_size=10)
    logger.info("Connected to PostgreSQL")
    return pool


async def run_migrations() -> None:
    async with pool.acquire() as conn:
        await conn.execute(MIGRATIONS)

    # Idempotent ALTER TABLE for columns added after initial schema — safe every startup
    alter_columns = [
        "ALTER TABLE schedules ADD COLUMN IF NOT EXISTS parse_mode TEXT DEFAULT NULL",
        "ALTER TABLE schedules ADD COLUMN IF NOT EXISTS silent BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE schedules ADD COLUMN IF NOT EXISTS buttons_json TEXT DEFAULT NULL",
        "ALTER TABLE send_log ADD COLUMN IF NOT EXISTS message_id BIGINT",
    ]
    async with pool.acquire() as conn:
        for stmt in alter_columns:
            await conn.execute(stmt)

    logger.info("Migrations complete")


async def close() -> None:
    global pool
    if pool:
        await pool.close()
        logger.info("PostgreSQL pool closed")


def get_pool() -> asyncpg.Pool:
    assert pool is not None, "DB pool not initialized — call db.connect() first"
    return pool
