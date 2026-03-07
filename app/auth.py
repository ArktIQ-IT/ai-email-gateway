from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from argon2 import PasswordHasher
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, load_accounts_config


@dataclass
class AuthContext:
    key_id: str
    allowed_accounts: set[str]


ph = PasswordHasher()
settings = Settings()


class RateLimiter:
    def __init__(self):
        self.calls = defaultdict(deque)

    def check(self, key: str, limit: int, window_s: int = 60):
        now = time.time()
        q = self.calls[key]
        while q and q[0] < now - window_s:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(status_code=429, detail="rate_limited")
        q.append(now)


rl = RateLimiter()
bearer_auth = HTTPBearer(auto_error=False)


def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_auth),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token")
    supplied = credentials.credentials.strip()
    if not supplied:
        raise HTTPException(status_code=401, detail="empty_token")

    cfg = load_accounts_config()
    matched = None
    for key in cfg.api_keys:
        try:
            if ph.verify(key.key_hash, supplied):
                matched = key
                break
        except Exception:
            continue

    # Constant-time check guard.
    if matched is None and cfg.api_keys:
        secrets.compare_digest(supplied.encode(), b"invalid")

    if matched is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_api_key")

    rl.check(f"{matched.key_id}:all", settings.rate_limit_per_minute)
    return AuthContext(key_id=matched.key_id, allowed_accounts=set(matched.accounts))


def require_job_rate_limit(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    rl.check(f"{ctx.key_id}:jobs", settings.rate_limit_jobs_per_minute)
    return ctx
