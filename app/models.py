from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    canceled = "canceled"


class AccountsCacheState(Base):
    __tablename__ = "accounts_cache_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(255), index=True)
    folder: Mapped[str] = mapped_column(String(255), index=True)
    uidvalidity: Mapped[int] = mapped_column(Integer)
    last_sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class MessageIndex(Base):
    __tablename__ = "message_index"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(255), index=True)
    folder: Mapped[str] = mapped_column(String(255), index=True)
    uidvalidity: Mapped[int] = mapped_column(Integer)
    uid: Mapped[int] = mapped_column(Integer)
    internal_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    from_: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    to: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    cc: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    message_id_header: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    direction: Mapped[str] = mapped_column(String(16), default="incoming")

    __table_args__ = (
        UniqueConstraint("account_id", "folder", "uidvalidity", "uid", name="uq_msg_uid"),
        Index("ix_message_index_acc_folder_internal_date", "account_id", "folder", "internal_date"),
        Index("ix_message_index_acc_internal_date", "account_id", "internal_date"),
    )


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    api_key_id: Mapped[str] = mapped_column(String(255), index=True)
    account_id: Mapped[str] = mapped_column(String(255), index=True)
    folder: Mapped[str] = mapped_column(String(255), index=True)
    since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    operation: Mapped[str] = mapped_column(String(64))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    dedupe_key: Mapped[str] = mapped_column(String(1024), index=True)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Lock(Base):
    __tablename__ = "locks"

    lock_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(64), default="running")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
