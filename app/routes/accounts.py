from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthContext, get_auth_context
from app.config import load_accounts_config

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


@router.get(
    "",
    summary="List accessible accounts",
    description="Return only accounts that are allowed for the current API key.",
)
def list_accounts(ctx: AuthContext = Depends(get_auth_context)):
    cfg = load_accounts_config()
    visible = []
    for account_id in ctx.allowed_accounts:
        acc = cfg.accounts.get(account_id)
        if not acc:
            continue
        visible.append(
            {
                "account_id": account_id,
                "provider": acc.provider,
                "folders_read": acc.folders_read,
                "drafts_folder_default": acc.drafts_folder_default,
            }
        )
    return {"accounts": visible}


def validate_account_access(ctx: AuthContext, account_id: str):
    if account_id not in ctx.allowed_accounts:
        raise HTTPException(status_code=403, detail="account_forbidden")
