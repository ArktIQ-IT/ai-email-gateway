from __future__ import annotations

import logging

from fastapi import FastAPI

from app.db import (
    Base,
    backfill_message_index_metadata,
    engine,
    ensure_sqlite_schema_compat,
    sqlite_message_index_needs_backfill,
)
from app.jobs.queue import ensure_worker_started
from app.jobs.scheduler import ensure_auto_sync_started
from app.routes import accounts, drafts, jobs, messages, sync

logger = logging.getLogger(__name__)

app = FastAPI(
    title="A safer AI e-mail gateway",
    version="0.1.0",
    description=(
        "A safer IMAP-backed gateway for AI-assisted email workflows.\n\n"
        "This service syncs messages from configured mailboxes into a local cache, "
        "then exposes filtered read APIs and draft creation without exposing IMAP credentials "
        "to AI clients.\n\n"
        "Core flow:\n"
        "1. Configure accounts and API-key allowlists in `config/accounts.yaml`.\n"
        "2. Sync mailbox data into cache (automatic scheduler or manual `/sync`).\n"
        "3. Query cached messages via `/messages:list` and fetch one via `/messages:get`.\n"
        "4. Create draft emails with `/drafts`.\n\n"
        "Authentication: Bearer API key.\n"
        "Canonical message ID format: `folder|uidvalidity|uid`."
    ),
)


@app.on_event("startup")
async def startup() -> None:
    Base.metadata.create_all(bind=engine)
    schema_changed = ensure_sqlite_schema_compat()
    if schema_changed or sqlite_message_index_needs_backfill():
        result = backfill_message_index_metadata()
        if result["updated"] > 0:
            logger.info("Backfilled message index metadata", extra=result)
    await ensure_worker_started()
    await ensure_auto_sync_started()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


app.include_router(accounts.router)
app.include_router(messages.router)
app.include_router(jobs.router)
app.include_router(drafts.router)
app.include_router(sync.router)
