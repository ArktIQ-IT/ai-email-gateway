from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.config import Settings, load_accounts_config
from app.db import SessionLocal
from app.imap.sync import list_messages, sync_messages_to_cache
from app.models import Job, JobStatus, Lock


settings = Settings()
queue: asyncio.Queue[str] = asyncio.Queue()
worker_started = False


def _utcnow() -> datetime:
    # SQLite returns naive datetimes by default, so keep queue timestamps naive UTC
    # to avoid offset-aware/naive comparison TypeError in worker checks.
    return datetime.utcnow()


def dedupe_key(payload: dict[str, Any]) -> str:
    keys = [
        "account_id",
        "folders",
        "since",
        "until",
        "sync_mode",
        "output_format",
        "limit",
        "cursor",
        "operation",
        "include_subfolders",
    ]
    return "|".join(f"{k}={payload.get(k)}" for k in keys)


def enqueue_job(job_id: str) -> None:
    queue.put_nowait(job_id)


def create_or_reuse_job(db: Session, api_key_id: str, payload: dict[str, Any]) -> Job:
    dk = dedupe_key(payload)
    existing = db.scalar(
        select(Job).where(and_(Job.dedupe_key == dk, Job.status.in_([JobStatus.queued, JobStatus.running])))
    )
    if existing:
        return existing

    payload_folders = payload.get("folders") or []
    if isinstance(payload_folders, list):
        primary_folder = payload_folders[0] if payload_folders else payload.get("folder", "*")
    else:
        primary_folder = payload.get("folder", "*")
    lock_key = f"account:{payload['account_id']}:folder:{primary_folder}"
    lock = db.get(Lock, lock_key)
    if lock and lock.expires_at > _utcnow():
        running = db.get(Job, lock.job_id)
        if running and running.status in [JobStatus.queued, JobStatus.running]:
            return running

    job = Job(
        api_key_id=api_key_id,
        account_id=payload["account_id"],
        folder=primary_folder,
        since=payload.get("since"),
        until=payload.get("until"),
        operation=payload.get("operation", "messages_list"),
        status=JobStatus.queued,
        progress=0,
        dedupe_key=dk,
        params_json=json.dumps(payload, default=str),
        expires_at=_utcnow() + timedelta(minutes=settings.job_ttl_minutes),
    )
    db.add(job)
    db.flush()

    db.merge(
        Lock(
            lock_key=lock_key,
            job_id=job.job_id,
            status="running",
            expires_at=_utcnow() + timedelta(seconds=settings.lock_ttl_seconds),
        )
    )
    db.commit()
    db.refresh(job)
    enqueue_job(job.job_id)
    return job


async def worker_loop() -> None:
    while True:
        job_id = await queue.get()
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            if not job:
                continue
            if job.expires_at < _utcnow():
                job.status = JobStatus.canceled
                db.commit()
                continue
            job.status = JobStatus.running
            job.progress = 10
            db.commit()

            cfg = load_accounts_config()
            account = cfg.accounts[job.account_id]

            params = json.loads(job.params_json or "{}")
            if job.operation == "sync_account":
                folders_param = params.get("folders")
                folders = folders_param if isinstance(folders_param, list) and folders_param else [job.folder]
                data = sync_messages_to_cache(
                    db=db,
                    account_id=job.account_id,
                    account=account,
                    folders=folders,
                    since=job.since,
                    until=job.until,
                    include_subfolders=bool(params.get("include_subfolders", True)),
                    limit_per_folder=int(params.get("limit", 500)),
                )
            else:
                data = list_messages(
                    account_id=job.account_id,
                    account=account,
                    folder=job.folder,
                    since=job.since,
                    until=job.until,
                    limit=int(params.get("limit", 50)),
                    output_format=params.get("output_format", "text"),
                )
            job.result_json = json.dumps(data)
            job.status = JobStatus.done
            job.progress = 100
            job.updated_at = _utcnow()
            db.commit()
        except Exception as exc:
            if job:
                job.status = JobStatus.failed
                job.error = f"job_failed:{exc.__class__.__name__}"
                job.updated_at = _utcnow()
                db.commit()
        finally:
            db.close()
            queue.task_done()


async def ensure_worker_started() -> None:
    global worker_started
    if not worker_started:
        asyncio.create_task(worker_loop())
        worker_started = True
