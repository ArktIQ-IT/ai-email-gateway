from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, not_, or_, select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context
from app.config import load_accounts_config
from app.db import get_db
from app.email_safety import build_thread_key
from app.models import MessageIndex
from app.routes.accounts import validate_account_access

router = APIRouter(prefix="/v1/accounts", tags=["messages"])


def _source_id(folder: str, uidvalidity: int, uid: int) -> str:
    return f"{folder}|{uidvalidity}|{uid}"


def _parse_source_id(value: str) -> tuple[str, int, int] | None:
    parts = value.rsplit("|", 2)
    if len(parts) != 3:
        return None
    folder, uidvalidity_raw, uid_raw = parts
    if not folder:
        return None
    try:
        uidvalidity = int(uidvalidity_raw)
        uid = int(uid_raw)
    except ValueError:
        return None
    return folder, uidvalidity, uid


class MessagesListRequest(BaseModel):
    folders: list[str] = Field(default_factory=list)
    since: datetime | None = Field(default=None)
    until: datetime | None = Field(default=None)
    limit: int = Field(default=50)
    offset: int = Field(default=0)
    senders: list[str] = Field(default_factory=list)
    recipients: list[str] = Field(default_factory=list)
    free_text: list[str] = Field(default_factory=list)
    direction: Literal["incoming", "sent", "unknown", "all"] = Field(default="all")
    include_body: bool = Field(default=True, description="Include cleaned body text.")
    include_raw_body: bool = Field(default=False, description="Include unsanitized body text from cache.")
    exclude_suspicious: bool = Field(default=True, description="Hide suspicious messages by default.")


class MessageGetRequest(BaseModel):
    id: str = Field(examples=["INBOX|12345|67890"])
    include_raw_body: bool = Field(default=False)


class MessageThreadRequest(BaseModel):
    id: str = Field(description="Canonical message ID from messages:list")
    limit: int = Field(default=100, ge=1, le=500)
    include_body: bool = Field(default=True)


def _serialize_row(row: MessageIndex, include_body: bool, include_raw_body: bool) -> dict:
    safety = json.loads(row.safety_flags_json or '{"score":0,"is_suspicious":false,"findings":[]}')
    item = {
        "id": _source_id(row.folder, row.uidvalidity, row.uid),
        "account_id": row.account_id,
        "folder": row.folder,
        "uidvalidity": row.uidvalidity,
        "uid": row.uid,
        "direction": row.direction,
        "internal_date": row.internal_date,
        "from": row.from_,
        "to": row.to,
        "cc": row.cc,
        "subject": row.subject,
        "message_id": row.message_id_header,
        "in_reply_to": row.in_reply_to_header,
        "thread_key": row.thread_key,
        "size": row.size,
        "flags": json.loads(row.flags or "[]"),
        "safety": safety,
    }
    if include_body:
        item["body_text"] = row.body_text_clean or row.body_text
    if include_raw_body:
        item["body_text_raw"] = row.body_text
    return item


@router.post("/{account_id}/messages:list")
def list_cached_messages(
    account_id: str,
    req: MessagesListRequest,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    validate_account_access(ctx, account_id)
    cfg = load_accounts_config()
    if account_id not in cfg.accounts:
        raise HTTPException(status_code=404, detail="account_not_found")

    if req.limit > 500:
        raise HTTPException(status_code=400, detail="limit_too_high")
    if req.offset < 0:
        raise HTTPException(status_code=400, detail="invalid_offset")
    if req.since and req.until and req.since >= req.until:
        raise HTTPException(status_code=400, detail="invalid_date_range")

    stmt = select(MessageIndex).where(MessageIndex.account_id == account_id)
    count_stmt = select(func.count(MessageIndex.id)).where(MessageIndex.account_id == account_id)

    if req.folders:
        stmt = stmt.where(MessageIndex.folder.in_([v for v in req.folders if v]))
        count_stmt = count_stmt.where(MessageIndex.folder.in_([v for v in req.folders if v]))
    if req.since:
        stmt = stmt.where(MessageIndex.internal_date >= req.since)
        count_stmt = count_stmt.where(MessageIndex.internal_date >= req.since)
    if req.until:
        stmt = stmt.where(MessageIndex.internal_date < req.until)
        count_stmt = count_stmt.where(MessageIndex.internal_date < req.until)
    if req.direction != "all":
        stmt = stmt.where(MessageIndex.direction == req.direction)
        count_stmt = count_stmt.where(MessageIndex.direction == req.direction)
    if req.senders:
        sender_expr = or_(*[MessageIndex.from_.ilike(f"%{value}%") for value in req.senders])
        stmt = stmt.where(sender_expr)
        count_stmt = count_stmt.where(sender_expr)
    if req.recipients:
        recipient_expr = or_(*[MessageIndex.to.ilike(f"%{v}%") | MessageIndex.cc.ilike(f"%{v}%") for v in req.recipients])
        stmt = stmt.where(recipient_expr)
        count_stmt = count_stmt.where(recipient_expr)
    if req.free_text:
        text_expr = or_(*[MessageIndex.subject.ilike(f"%{v}%") | MessageIndex.body_text_clean.ilike(f"%{v}%") for v in req.free_text])
        stmt = stmt.where(text_expr)
        count_stmt = count_stmt.where(text_expr)

    raw_count_stmt = count_stmt
    if req.exclude_suspicious:
        suspicious_expr = or_(
            MessageIndex.safety_flags_json.like('%"is_suspicious":true%'),
            MessageIndex.safety_flags_json.like('%"is_suspicious": true%'),
        )
        safe_expr = or_(MessageIndex.safety_flags_json.is_(None), not_(suspicious_expr))
        stmt = stmt.where(safe_expr)
        count_stmt = count_stmt.where(safe_expr)

    rows = db.scalars(stmt.order_by(MessageIndex.internal_date.desc()).offset(req.offset).limit(req.limit)).all()
    total = db.scalar(count_stmt) or 0
    raw_total = db.scalar(raw_count_stmt) or total

    messages = []
    for row in rows:
        messages.append(_serialize_row(row, include_body=req.include_body, include_raw_body=req.include_raw_body))

    suspicious_filtered = max(raw_total - total, 0) if req.exclude_suspicious else 0

    return {
        "messages": messages,
        "total": total,
        "limit": req.limit,
        "offset": req.offset,
        "suspicious_filtered": suspicious_filtered,
    }


@router.post("/{account_id}/messages:get")
def get_cached_message(
    account_id: str,
    req: MessageGetRequest,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    validate_account_access(ctx, account_id)
    cfg = load_accounts_config()
    if account_id not in cfg.accounts:
        raise HTTPException(status_code=404, detail="account_not_found")

    source = _parse_source_id(req.id)
    if source is None:
        raise HTTPException(status_code=400, detail="invalid_message_id_format")
    folder, uidvalidity, uid = source

    row = db.scalar(
        select(MessageIndex).where(
            MessageIndex.account_id == account_id,
            MessageIndex.folder == folder,
            MessageIndex.uidvalidity == uidvalidity,
            MessageIndex.uid == uid,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="message_not_found")

    return _serialize_row(row, include_body=True, include_raw_body=req.include_raw_body)


@router.post("/{account_id}/messages:thread")
def get_thread_messages(
    account_id: str,
    req: MessageThreadRequest,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    validate_account_access(ctx, account_id)
    cfg = load_accounts_config()
    if account_id not in cfg.accounts:
        raise HTTPException(status_code=404, detail="account_not_found")

    source = _parse_source_id(req.id)
    if source is None:
        raise HTTPException(status_code=400, detail="invalid_message_id_format")

    folder, uidvalidity, uid = source
    seed = db.scalar(
        select(MessageIndex).where(
            MessageIndex.account_id == account_id,
            MessageIndex.folder == folder,
            MessageIndex.uidvalidity == uidvalidity,
            MessageIndex.uid == uid,
        )
    )
    if seed is None:
        raise HTTPException(status_code=404, detail="message_not_found")

    thread_key = seed.thread_key or build_thread_key(
        seed.message_id_header,
        seed.in_reply_to_header,
        seed.references_header,
        seed.subject,
    )

    rows = db.scalars(
        select(MessageIndex)
        .where(
            MessageIndex.account_id == account_id,
            or_(
                MessageIndex.thread_key == thread_key,
                MessageIndex.id == seed.id,
            ),
        )
        .order_by(MessageIndex.internal_date.asc())
        .limit(req.limit)
    ).all()

    return {
        "account_id": account_id,
        "thread_key": thread_key,
        "messages": [_serialize_row(row, include_body=req.include_body, include_raw_body=False) for row in rows],
        "count": len(rows),
    }
