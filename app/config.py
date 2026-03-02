from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class APIKeyConfig(BaseModel):
    key_id: str
    key_hash: str
    accounts: list[str] = Field(default_factory=list)


class AccountConfig(BaseModel):
    provider: Literal["domeneshop", "proton_bridge", "generic_imap"] = "generic_imap"
    imap_host: str
    imap_port: int = 993
    imap_tls: Literal["ssl", "starttls"] = "ssl"
    imap_username: str
    imap_password_env: str
    folders_read: list[str] = Field(default_factory=lambda: ["INBOX"])
    drafts_folder_default: str = "Drafts"

    @field_validator("imap_password_env")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("imap_password_env cannot be empty")
        return value


class AccountsFile(BaseModel):
    api_keys: list[APIKeyConfig] = Field(default_factory=list)
    accounts: dict[str, AccountConfig] = Field(default_factory=dict)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/app.db"
    log_level: str = "INFO"
    request_max_bytes: int = 1024 * 1024
    imap_timeout_seconds: int = 30
    max_lookback_days: int = 365
    default_range_hours: int = 24
    max_limit: int = 200
    allow_raw: bool = False
    cache_body: bool = False
    job_ttl_minutes: int = 60
    lock_ttl_seconds: int = 900

    rate_limit_per_minute: int = 120
    rate_limit_jobs_per_minute: int = 30

    global_imap_sessions: int = 10
    account_imap_sessions: int = 1


def load_accounts_config(path: str = "config/accounts.yaml") -> AccountsFile:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file does not exist: {path}")
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    model = AccountsFile.model_validate(raw)

    for account_id, account in model.accounts.items():
        env_name = account.imap_password_env
        if env_name not in os.environ:
            raise ValueError(
                f"Missing env var {env_name} for account {account_id}. "
                "IMAP secrets must come from environment variables."
            )
    return model
