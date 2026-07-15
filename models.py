"""Dataclasses for schedule, target, and send-log records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Schedule:
    id: int
    created_by: int
    target_chat_id: int
    target_label: Optional[str]
    message_type: str  # text, photo, document
    content_text: Optional[str]
    file_id: Optional[str]
    schedule_type: str  # once, daily, weekly, interval, cron
    cron_expression: Optional[str]
    run_at: Optional[datetime]
    timezone: str
    is_active: bool
    is_paused: bool
    created_at: datetime
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    parse_mode: Optional[str] = None
    silent: bool = False
    buttons_json: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict) -> Schedule:
        return cls(
            id=row["id"],
            created_by=row["created_by"],
            target_chat_id=row["target_chat_id"],
            target_label=row.get("target_label"),
            message_type=row["message_type"],
            content_text=row.get("content_text"),
            file_id=row.get("file_id"),
            schedule_type=row["schedule_type"],
            cron_expression=row.get("cron_expression"),
            run_at=row.get("run_at"),
            timezone=row["timezone"],
            is_active=row["is_active"],
            is_paused=row["is_paused"],
            created_at=row["created_at"],
            last_run_at=row.get("last_run_at"),
            next_run_at=row.get("next_run_at"),
            parse_mode=row.get("parse_mode"),
            silent=row.get("silent", False),
            buttons_json=row.get("buttons_json"),
        )


@dataclass
class Target:
    id: int
    chat_id: int
    label: str
    added_by: int
    added_at: datetime

    @classmethod
    def from_row(cls, row: dict) -> Target:
        return cls(
            id=row["id"],
            chat_id=row["chat_id"],
            label=row["label"],
            added_by=row["added_by"],
            added_at=row["added_at"],
        )


@dataclass
class SendLog:
    id: int
    schedule_id: Optional[int]
    target_chat_id: int
    status: str
    error_message: Optional[str]
    message_id: Optional[int]
    sent_at: datetime
