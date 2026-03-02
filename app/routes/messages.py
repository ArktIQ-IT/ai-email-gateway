from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import AuthContext, require_job_rate_limit
from app.config import Settings, load_accounts_config
from app.db import get_db
from app.jobs.queue import create_or_reuse_job
from app.routes.accounts import validate_account_access

router = APIRouter(prefix="/v1/accounts", tags=["messages"])


class MessagesListRequest(BaseModel):
    folder: str = "INBOX"
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 50
    cursor: str | None = None
    sync_mode: Literal["none", "incremental", "full"] = "incremental"
    output_format: Literal["text", "text+attachments-metadata", "raw"] = "text"
    reconcile_deletions: bool = False


class MessageGetRequest(BaseModel):
    folder: str = "INBOX"
    message_id: str
    output_format: Literal["text", "text+attachments-metadata", "raw"] = "text"


@router.post("/{account_id}/messages:list", status_code=202)
def start_messages_list_job(
    account_id: str,
    req: MessagesListRequest,
    ctx: AuthContext = Depends(require_job_rate_limit),
    db: Session = Depends(get_db),
):
    settings = Settings()
    validate_account_access(ctx, account_id)
    cfg = load_accounts_config()
    acc = cfg.accounts.get(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="account_not_found")
    if req.folder not in acc.folders_read:
        raise HTTPException(status_code=400, detail="folder_not_allowed")

    now = datetime.now(timezone.utc)
    since = req.since or now - timedelta(hours=settings.default_range_hours)
    until = req.until or now

    if since >= until:
        raise HTTPException(status_code=400, detail="invalid_date_range")
    if since < now - timedelta(days=settings.max_lookback_days):
        raise HTTPException(status_code=400, detail="since_too_old")
    if req.limit > settings.max_limit:
        raise HTTPException(status_code=400, detail="limit_too_high")
    if req.output_format == "raw" and not settings.allow_raw:
        raise HTTPException(status_code=400, detail="raw_output_disabled")

    payload = {
        "operation": "messages_list",
        "account_id": account_id,
        "folder": req.folder,
        "since": since,
        "until": until,
        "limit": req.limit,
        "cursor": req.cursor,
        "sync_mode": req.sync_mode,
        "output_format": req.output_format,
    }
    job = create_or_reuse_job(db, ctx.key_id, payload)
    return {"job_id": job.job_id, "status": job.status.value, "poll_url": f"/v1/jobs/{job.job_id}"}


@router.post("/{account_id}/messages:get", status_code=202)
def start_message_get_job(
    account_id: str,
    req: MessageGetRequest,
    ctx: AuthContext = Depends(require_job_rate_limit),
    db: Session = Depends(get_db),
):
    validate_account_access(ctx, account_id)
    cfg = load_accounts_config()
    acc = cfg.accounts.get(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="account_not_found")
    if req.folder not in acc.folders_read:
        raise HTTPException(status_code=400, detail="folder_not_allowed")
    payload = {
        "operation": "message_get",
        "account_id": account_id,
        "folder": req.folder,
        "since": datetime.now(timezone.utc) - timedelta(days=1),
        "until": datetime.now(timezone.utc),
        "limit": 1,
        "cursor": req.message_id,
        "sync_mode": "none",
        "output_format": req.output_format,
    }
    job = create_or_reuse_job(db, ctx.key_id, payload)
    return {"job_id": job.job_id, "status": job.status.value, "poll_url": f"/v1/jobs/{job.job_id}"}
