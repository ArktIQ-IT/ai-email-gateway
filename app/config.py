from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_dotenv(path: str = ".env") -> dict[str, str]:
    env_file = Path(path)
    if not env_file.exists():
        return {}

    values: dict[str, str] = {}
    with env_file.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue

            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value
    return values


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
    auto_sync_enabled: bool = True
    auto_sync_interval_minutes: int = 15
    auto_sync_lookback_minutes: int | None = None
    auto_sync_include_subfolders: bool = True
    auto_sync_limit_per_folder: int = 500

    @field_validator("imap_password_env")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("imap_password_env cannot be empty")
        return value

    @field_validator("auto_sync_interval_minutes")
    @classmethod
    def _valid_sync_interval(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("auto_sync_interval_minutes must be > 0")
        return value

    @field_validator("auto_sync_lookback_minutes")
    @classmethod
    def _valid_sync_lookback(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("auto_sync_lookback_minutes must be > 0")
        return value

    @field_validator("auto_sync_limit_per_folder")
    @classmethod
    def _valid_sync_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("auto_sync_limit_per_folder must be > 0")
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
    dotenv_values = _read_dotenv(".env")

    for account_id, account in model.accounts.items():
        env_name = account.imap_password_env
        if env_name not in os.environ:
            dotenv_value = dotenv_values.get(env_name)
            if dotenv_value is not None:
                os.environ[env_name] = dotenv_value
                continue
            raise ValueError(
                f"Missing env var {env_name} for account {account_id}. "
                "IMAP secrets must come from environment variables."
            )
    return model
