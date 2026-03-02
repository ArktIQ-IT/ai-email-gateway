from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import AuthContext, get_auth_context
from app.db import get_db
from app.models import Job, JobStatus

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(job_id: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_not_found")
    if job.api_key_id != ctx.key_id:
        raise HTTPException(status_code=403, detail="job_forbidden")

    response = {
        "job_id": job.job_id,
        "status": job.status.value,
        "progress": job.progress,
        "created_at": job.created_at,
        "expires_at": job.expires_at,
    }
    if job.status == JobStatus.done and job.result_json:
        response["result"] = json.loads(job.result_json)
    if job.status == JobStatus.failed:
        response["error_code"] = "job_failed"
        response["error_message"] = job.error or "unknown"
    return response


@router.get("/{job_id}/wait")
async def wait_job(
    job_id: str,
    timeout: int = Query(default=20, ge=1, le=120),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job_not_found")
        if job.api_key_id != ctx.key_id:
            raise HTTPException(status_code=403, detail="job_forbidden")
        if job.status in [JobStatus.done, JobStatus.failed, JobStatus.canceled]:
            return get_job(job_id, ctx, db)
        await asyncio.sleep(0.5)
        db.expire_all()
    return {"job_id": job_id, "status": "running", "progress": 0}
