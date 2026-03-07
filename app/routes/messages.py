from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context
from app.config import load_accounts_config
from app.db import get_db
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
    folders: list[str] = Field(
        default_factory=list,
        description="Filter by folder names (OR). Empty means all cached folders.",
    )
    since: datetime | None = Field(default=None, description="Include messages with internal date >= since.")
    until: datetime | None = Field(default=None, description="Include messages with internal date < until.")
    limit: int = Field(default=50, description="Maximum messages to return (max 500).")
    offset: int = Field(default=0, description="Pagination offset.")
    senders: list[str] = Field(default_factory=list, description="Sender text filters (OR).")
    recipients: list[str] = Field(
        default_factory=list,
        description="Recipient filters (OR), matched against To and Cc fields.",
    )
    free_text: list[str] = Field(
        default_factory=list,
        description="Free-text filters (OR), matched against subject and body_text.",
    )
    direction: Literal["incoming", "sent", "unknown", "all"] = Field(
        default="all",
        description="Filter by inferred message direction.",
    )
    include_body: bool = Field(default=True, description="Include cached body_text in each result item.")


class MessageGetRequest(BaseModel):
    id: str = Field(
        description="Canonical cached message ID from messages:list. Format: folder|uidvalidity|uid.",
        examples=["INBOX|12345|67890"],
    )


@router.post(
    "/{account_id}/messages:list",
    summary="List cached messages",
    description=(
        "Query cached messages for an account with date, folder, sender, recipient, "
        "direction, and free-text filters. All list filters are OR within the same field."
    ),
)
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

    folder_filters = [v for v in req.folders if v]
    if folder_filters:
        stmt = stmt.where(MessageIndex.folder.in_(folder_filters))
        count_stmt = count_stmt.where(MessageIndex.folder.in_(folder_filters))
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
        recipient_expr = or_(
            *(
                MessageIndex.to.ilike(f"%{value}%") | MessageIndex.cc.ilike(f"%{value}%")
                for value in req.recipients
            )
        )
        stmt = stmt.where(recipient_expr)
        count_stmt = count_stmt.where(recipient_expr)
    if req.free_text:
        text_expr = or_(
            *(
                MessageIndex.subject.ilike(f"%{value}%") | MessageIndex.body_text.ilike(f"%{value}%")
                for value in req.free_text
            )
        )
        stmt = stmt.where(text_expr)
        count_stmt = count_stmt.where(text_expr)

    rows = db.scalars(
        stmt.order_by(MessageIndex.internal_date.desc()).offset(req.offset).limit(req.limit)
    ).all()
    total = db.scalar(count_stmt) or 0

    messages = []
    for row in rows:
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
            "size": row.size,
            "flags": json.loads(row.flags or "[]"),
        }
        if req.include_body:
            item["body_text"] = row.body_text
        messages.append(item)
    return {"messages": messages, "total": total, "limit": req.limit, "offset": req.offset}


@router.post(
    "/{account_id}/messages:get",
    summary="Get one cached message",
    description="Fetch a single cached message by canonical ID (folder|uidvalidity|uid).",
)
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

    row = None
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

    return {
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
        "size": row.size,
        "flags": json.loads(row.flags or "[]"),
        "body_text": row.body_text,
    }
