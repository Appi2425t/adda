# Telegram Auto-Scheduler Bot

A production-ready Telegram bot for scheduling messages to be sent automatically — one-time or recurring — with persistence in PostgreSQL and APScheduler.

## Features

- **Scheduled auto-messaging** — one-time, daily, weekly, interval, or custom cron
- **Multi-target broadcast** — send the same message to multiple chats at once
- **Media support** — text, photos, documents with captions
- **Inline buttons** — attach URL/callback buttons to scheduled messages
- **Silent messages** — schedule messages without notification sound
- **Parse mode** — HTML or Markdown formatting for rich text
- **Admin permissions** — only authorized user IDs can manage schedules
- **Blacklist** — prevent the bot from sending to specific chats
- **Templates** — save and reuse frequently sent messages
- **Edit schedules** — modify content, target, or timing of existing schedules
- **Test send** — verify delivery before going live
- **Export/Import** — backup and restore schedules as JSON
- **Duplicate detection** — find and manage duplicate schedules
- **Send logs** — full audit trail with history viewer
- **Rate limiting** — per-chat throttling to avoid Telegram flood limits
- **Auto-cleanup** — prune old send logs
- **Persistence** — survives restarts/redeploys via PostgreSQL job store
- **Graceful shutdown** — handles SIGTERM from Railway

## Project Structure

```
Procfile               # Railway entrypoint
railway.toml           # Railway build/deploy config
main.py                # Entrypoint: init bot, DB, scheduler
config.py              # Env var loading + auto-detection
db.py                  # Asyncpg pool + migrations
scheduler.py           # APScheduler + rate limiter + blacklisting
models.py              # Dataclasses
handlers/
  __init__.py
  schedule.py          # /schedule conversation flow
  broadcast.py         # /broadcast multi-target send
  edit.py              # /edit modify existing schedules
  templates.py         # /savetemplate, /templates, /usetemplate
  manage.py            # /schedules, /cancel, /pause, /resume, /preview, /test, /next, /logs, /export, /import
  blacklist.py         # /blacklistadd, /unblacklist, /blacklist
  admin.py             # /whoami, permission checks
  misc.py              # /start, /help, /clist, /stats, /cleanup
requirements.txt
runtime.txt
.env.example
README.md
```

## Commands

### Scheduling
| Command | Description |
|---------|-------------|
| `/schedule` | Create a new scheduled message (interactive) |
| `/schedules` | List all active schedules |
| `/edit <id>` | Edit an existing schedule |
| `/cancel <id>` | Cancel a schedule |
| `/pause <id>` | Pause a schedule |
| `/resume <id>` | Resume a paused schedule |
| `/preview <id>` | Preview what a schedule sends |
| `/test <id>` | Send a test message to verify delivery |
| `/next` | Show next 10 upcoming sends |

### Broadcast & Templates
| Command | Description |
|---------|-------------|
| `/broadcast` | Send a message to multiple targets at once |
| `/savetemplate` | Save a reusable message template |
| `/templates` | List saved templates |
| `/usetemplate <name>` | Load a template for /schedule |
| `/deletetemplate <name>` | Delete a template |

### Targets
| Command | Description |
|---------|-------------|
| `/addtarget <chat_id> <label>` | Save a target chat |
| `/removetarget <label>` | Remove a saved target |
| `/targets` | List all saved targets |

### Safety
| Command | Description |
|---------|-------------|
| `/blacklistadd <chat_id> [reason]` | Block bot from a chat |
| `/unblacklist <chat_id>` | Unblock a chat |
| `/blacklist` | List blacklisted chats |
| `/duplicates` | Check for duplicate schedules |

### Data
| Command | Description |
|---------|-------------|
| `/export` | Export all schedules as JSON |
| `/import` | Import schedules from a JSON file (reply to file) |
| `/logs [hours]` | View send history (default: 24h) |
| `/cleanup [days]` | Delete old send logs (default: 30 days) |

### Info
| Command | Description |
|---------|-------------|
| `/whoami` | Get your Telegram user ID |
| `/stats` | View detailed statistics |
| `/help` | Show all commands |

## Database Tables

- **schedules** — all scheduled messages with metadata, parse mode, silent flag, inline buttons
- **targets** — saved chat/channel/group destinations
- **send_log** — audit trail of every send attempt with message IDs
- **blacklist** — chats the bot is blocked from sending to
- **templates** — reusable message templates
- **apscheduler_jobs** — APScheduler's internal job store (auto-created)

## Railway Setup

### 1. Create a new Railway service

1. Go to [railway.app](https://railway.app) and create a new project
2. Add a new service → select **"Deploy from GitHub repo"** or **"Empty Project"**

### 2. Attach the Postgres plugin

1. In your Railway project, click **"+ New"** → **"Database"** → **"PostgreSQL"**
2. Railway will provision a Postgres instance and automatically set `DATABASE_URL` in your service's environment variables
3. Go to your service's **Variables** tab and confirm `DATABASE_URL` appears

### 3. Set environment variables

In your service's **Variables** tab, add:

| Variable | Value |
|----------|-------|
| `BOT_TOKEN` | Your Telegram bot token from @BotFather |
| `ADMIN_IDS` | Your Telegram user ID (use `/whoami` with the bot first) |
| `DEFAULT_TIMEZONE` | Your timezone (e.g., `Asia/Kolkata`, `America/New_York`) |

`DATABASE_URL` is auto-injected by the Postgres plugin — do **not** set it manually.

### 4. Deploy

1. Push your code to the linked GitHub repo, or drag-and-drop the `bot/` folder
2. Railway will detect the `Procfile` and start the worker process
3. Check the **Deployments** tab for logs to confirm the bot started successfully

### 5. Verify

Send `/start` to your bot in Telegram. Then use `/whoami` to get your user ID and add it to `ADMIN_IDS` in Railway if you haven't already.

## Local Development

```bash
# Copy and fill in your env vars
cp .env.example .env
# Edit .env with your BOT_TOKEN, DATABASE_URL, ADMIN_IDS

# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

You need a local PostgreSQL instance or a remote one (e.g., via Railway's connect feature).

## How Scheduling Works

1. Admin runs `/schedule` and goes through the interactive flow
2. Schedule is saved to the `schedules` table in PostgreSQL
3. APScheduler (backed by `sqlalchemy` job store) persists the job
4. On bot restart, all active schedules are reloaded from DB into APScheduler
5. When a job fires:
   - Blacklist check → skip if blocked
   - Rate limit → throttle per-chat sends
   - Send message (text/photo/document with optional buttons and silent mode)
   - Log result to `send_log`
6. One-time schedules are deactivated after sending; recurring schedules update `next_run_at`

## Reliability Features

- **Blacklisting** — chats can be blocked; blocked sends are logged and skipped
- **Rate limiting** — 1-second minimum interval between sends to the same chat
- **Retry-on-failure** — all Telegram API calls wrapped in try/except
- **Send logging** — every attempt (success or failure) is recorded with error details
- **Duplicate detection** — `/duplicates` finds schedules with same target+content
- **Test mode** — `/test` sends a real message before committing to a schedule
- **Graceful shutdown** — SIGTERM handling lets in-flight sends complete
- **Auto-cleanup** — `/cleanup` prunes old logs to keep the database lean
