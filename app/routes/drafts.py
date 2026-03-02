from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import AuthContext, get_auth_context
from app.config import load_accounts_config
from app.imap.drafts import create_draft
from app.routes.accounts import validate_account_access

router = APIRouter(prefix="/v1/accounts", tags=["drafts"])


class Attachment(BaseModel):
    filename: str
    mime_type: str
    content_base64: str


class DraftCreateRequest(BaseModel):
    draft_folder: str | None = None
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str = ""
    text_body: str
    html_body: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)


@router.post("/{account_id}/drafts")
def drafts_create(account_id: str, req: DraftCreateRequest, ctx: AuthContext = Depends(get_auth_context)):
    validate_account_access(ctx, account_id)
    cfg = load_accounts_config()
    account = cfg.accounts.get(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="account_not_found")

    draft_folder = req.draft_folder or account.drafts_folder_default

    total_size = sum(len(a.content_base64) for a in req.attachments)
    if total_size > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="attachments_too_large")

    try:
        return create_draft(account, draft_folder, req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
