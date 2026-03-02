from __future__ import annotations

from fastapi import FastAPI

from app.db import Base, engine
from app.jobs.queue import ensure_worker_started
from app.routes import accounts, drafts, jobs, messages

app = FastAPI(title="Secure AI Email Gateway", version="0.1.0")


@app.on_event("startup")
async def startup() -> None:
    Base.metadata.create_all(bind=engine)
    await ensure_worker_started()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


app.include_router(accounts.router)
app.include_router(messages.router)
app.include_router(jobs.router)
app.include_router(drafts.router)
