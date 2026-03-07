from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.auth import AuthContext, require_job_rate_limit
from app.config import load_accounts_config
from app.db import get_db
from app.jobs.queue import create_or_reuse_job
from app.models import AccountsCacheState, MessageIndex
from app.routes.accounts import validate_account_access

router = APIRouter(prefix="/v1/accounts", tags=["sync"])


class SyncRequest(BaseModel):
    folders: list[str] = Field(
        default_factory=list,
        description="Root folders to sync (OR). Empty means account configured folders.",
        examples=[["INBOX", "Sent"]],
    )
    since: datetime | None = Field(default=None, description="Manual sync start datetime (inclusive).")
    until: datetime | None = Field(default=None, description="Manual sync end datetime (exclusive).")
    include_subfolders: bool = Field(default=True, description="Include subfolders under the root folder.")
    limit_per_folder: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Maximum messages fetched per folder per sync run.",
    )


class DeleteByIdsRequest(BaseModel):
    ids: list[str] = Field(
        default_factory=list,
        description="Canonical message IDs to delete. Format: folder|uidvalidity|uid.",
        examples=[["INBOX|12345|67890", "INBOX/Sub|12345|67891"]],
    )


class DeleteOlderThanRequest(BaseModel):
    older_than_days: int = Field(ge=1, le=36500, description="Delete cached messages older than this many days.")


class DeleteTimespanRequest(BaseModel):
    since: datetime | None = Field(default=None, description="Delete cached messages with internal_date >= since.")
    until: datetime | None = Field(default=None, description="Delete cached messages with internal_date < until.")


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


@router.get(
    "/{account_id}/sync:status",
    summary="Get sync status",
    description="Show scheduler configuration and last/next sync times per configured folder.",
)
def get_sync_status(
    account_id: str,
    ctx: AuthContext = Depends(require_job_rate_limit),
    db: Session = Depends(get_db),
):
    validate_account_access(ctx, account_id)
    cfg = load_accounts_config()
    acc = cfg.accounts.get(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="account_not_found")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    interval_minutes = acc.auto_sync_interval_minutes
    lookback_minutes = acc.auto_sync_lookback_minutes or (interval_minutes * 2)

    states = db.scalars(
        select(AccountsCacheState).where(AccountsCacheState.account_id == account_id)
    ).all()
    by_folder = {(s.folder or "").lower(): s for s in states}

    folders = list(dict.fromkeys(acc.folders_read))
    status_items = []
    for folder in folders:
        state = by_folder.get(folder.lower())
        last_sync_at = state.last_sync_at if state else None
        next_due_at = (last_sync_at + timedelta(minutes=interval_minutes)) if last_sync_at else now
        status_items.append(
            {
                "folder": folder,
                "last_sync_at": last_sync_at,
                "next_due_at": next_due_at,
                "interval_minutes": interval_minutes,
                "lookback_minutes": lookback_minutes,
                "auto_sync_enabled": acc.auto_sync_enabled,
                "include_subfolders": acc.auto_sync_include_subfolders,
                "limit_per_folder": acc.auto_sync_limit_per_folder,
            }
        )

    return {
        "account_id": account_id,
        "now": now,
        "scheduler": {"auto_sync_enabled": acc.auto_sync_enabled},
        "folders": status_items,
    }


@router.post(
    "/{account_id}/sync",
    status_code=202,
    summary="Start manual sync job",
    description=(
        "Enqueue an on-demand IMAP sync into the cache for the selected account.\n\n"
        "This operation is additive only: it inserts new cache rows and updates existing cached rows, "
        "but it does not delete cached messages and never deletes mailbox messages.\n\n"
        "For cache deletions, use the dedicated sync delete endpoints in this group: "
        "`/sync:delete-by-ids`, `/sync:delete-older-than`, or `/sync:delete-timespan`."
    ),
)
def start_sync_job(
    account_id: str,
    req: SyncRequest,
    ctx: AuthContext = Depends(require_job_rate_limit),
    db: Session = Depends(get_db),
):
    validate_account_access(ctx, account_id)
    cfg = load_accounts_config()
    acc = cfg.accounts.get(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="account_not_found")

    now = datetime.now(timezone.utc)
    since = req.since or now - timedelta(days=365)
    until = req.until or now
    if since >= until:
        raise HTTPException(status_code=400, detail="invalid_date_range")

    selected_folders = [value for value in req.folders if value] or list(acc.folders_read)
    selected_folders = list(dict.fromkeys(selected_folders))

    payload = {
        "operation": "sync_account",
        "account_id": account_id,
        "folders": selected_folders,
        "since": since,
        "until": until,
        "limit": req.limit_per_folder,
        "include_subfolders": req.include_subfolders,
    }
    job = create_or_reuse_job(db, ctx.key_id, payload)
    return {"job_id": job.job_id, "status": job.status.value, "poll_url": f"/v1/jobs/{job.job_id}"}


@router.post(
    "/{account_id}/sync:delete-by-ids",
    summary="Delete cached messages by IDs",
    description="Delete cached messages by canonical IDs from messages:list.",
)
def delete_messages_by_ids(
    account_id: str,
    req: DeleteByIdsRequest,
    ctx: AuthContext = Depends(require_job_rate_limit),
    db: Session = Depends(get_db),
):
    validate_account_access(ctx, account_id)
    if not req.ids:
        raise HTTPException(status_code=400, detail="ids_required")

    source_parts: list[tuple[str, int, int]] = []
    invalid_ids: list[str] = []
    for value in req.ids:
        parsed = _parse_source_id(value)
        if parsed is not None:
            source_parts.append(parsed)
            continue
        invalid_ids.append(value)

    if invalid_ids:
        raise HTTPException(status_code=400, detail={"error": "invalid_id_format", "ids": invalid_ids})

    clauses = []
    if source_parts:
        clauses.extend(
            and_(
                MessageIndex.folder == folder,
                MessageIndex.uidvalidity == uidvalidity,
                MessageIndex.uid == uid,
            )
            for folder, uidvalidity, uid in source_parts
        )
    if not clauses:
        raise HTTPException(status_code=400, detail="ids_required")

    result = db.execute(
        delete(MessageIndex).where(
            MessageIndex.account_id == account_id,
            or_(*clauses),
        )
    )
    db.commit()
    return {"deleted": int(result.rowcount or 0), "ids": req.ids}


@router.post(
    "/{account_id}/sync:delete-older-than",
    summary="Delete cached messages older than N days",
    description="Purge old cached messages for an account.",
)
def delete_messages_older_than(
    account_id: str,
    req: DeleteOlderThanRequest,
    ctx: AuthContext = Depends(require_job_rate_limit),
    db: Session = Depends(get_db),
):
    validate_account_access(ctx, account_id)
    cutoff = datetime.utcnow() - timedelta(days=req.older_than_days)
    result = db.execute(
        delete(MessageIndex).where(
            MessageIndex.account_id == account_id,
            MessageIndex.internal_date < cutoff,
        )
    )
    db.commit()
    return {"deleted": int(result.rowcount or 0), "older_than_days": req.older_than_days}


@router.post(
    "/{account_id}/sync:delete-timespan",
    summary="Delete cached messages in timespan",
    description="Delete cached messages by date interval.",
)
def delete_messages_by_timespan(
    account_id: str,
    req: DeleteTimespanRequest,
    ctx: AuthContext = Depends(require_job_rate_limit),
    db: Session = Depends(get_db),
):
    validate_account_access(ctx, account_id)
    if req.since is None and req.until is None:
        raise HTTPException(status_code=400, detail="timespan_required")
    if req.since and req.until and req.since >= req.until:
        raise HTTPException(status_code=400, detail="invalid_date_range")

    stmt = delete(MessageIndex).where(MessageIndex.account_id == account_id)
    if req.since:
        stmt = stmt.where(MessageIndex.internal_date >= req.since)
    if req.until:
        stmt = stmt.where(MessageIndex.internal_date < req.until)
    result = db.execute(stmt)
    db.commit()
    return {"deleted": int(result.rowcount or 0), "since": req.since, "until": req.until}
