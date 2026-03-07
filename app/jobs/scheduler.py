from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import AccountsFile, load_accounts_config
from app.db import SessionLocal
from app.jobs.queue import create_or_reuse_job
from app.models import AccountsCacheState


scheduler_started = False


def _utcnow() -> datetime:
    return datetime.utcnow()


def _pick_api_key_id(cfg: AccountsFile, account_id: str) -> str:
    for key in cfg.api_keys:
        if account_id in key.accounts:
            return key.key_id
    return "auto-sync"


def _schedule_sync_jobs(force: bool = False) -> None:
    cfg = load_accounts_config()
    db = SessionLocal()
    try:
        now = _utcnow()
        for account_id, account in cfg.accounts.items():
            if not account.auto_sync_enabled:
                continue

            interval = timedelta(minutes=account.auto_sync_interval_minutes)
            lookback_minutes = account.auto_sync_lookback_minutes or (account.auto_sync_interval_minutes * 2)
            for folder in account.folders_read:
                state = db.scalar(
                    select(AccountsCacheState).where(
                        AccountsCacheState.account_id == account_id,
                        AccountsCacheState.folder == folder,
                    )
                )
                if not force and state and (now - state.last_sync_at) < interval:
                    continue

                payload = {
                    "operation": "sync_account",
                    "account_id": account_id,
                    "folders": [folder],
                    "since": now - timedelta(minutes=lookback_minutes),
                    "until": now,
                    "limit": account.auto_sync_limit_per_folder,
                    "include_subfolders": account.auto_sync_include_subfolders,
                }
                create_or_reuse_job(db, _pick_api_key_id(cfg, account_id), payload)

                if state is None:
                    state = AccountsCacheState(
                        account_id=account_id,
                        folder=folder,
                        uidvalidity=0,
                        last_sync_at=now,
                        note="auto_sync",
                    )
                    db.add(state)
                else:
                    state.last_sync_at = now
                    state.note = "auto_sync"
                db.commit()
    finally:
        db.close()


async def auto_sync_loop() -> None:
    while True:
        try:
            _schedule_sync_jobs()
        except Exception:
            # Keep scheduler resilient to transient config/IMAP/DB errors.
            pass
        await asyncio.sleep(60)


async def ensure_auto_sync_started() -> None:
    global scheduler_started
    if not scheduler_started:
        # Always enqueue an immediate startup sync pass, then continue interval-based scheduling.
        try:
            _schedule_sync_jobs(force=True)
        except Exception:
            # Do not block API startup if scheduler bootstrap fails.
            pass
        asyncio.create_task(auto_sync_loop())
        scheduler_started = True
