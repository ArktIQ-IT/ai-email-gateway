from __future__ import annotations

import argparse
import secrets

from argon2 import PasswordHasher

from app.config import load_accounts_config


ph = PasswordHasher()


def gen_key() -> None:
    key = secrets.token_urlsafe(32)
    print("PLAINTEXT_API_KEY=", key)
    print("ARGON2_HASH=", ph.hash(key))


def check_config(path: str) -> None:
    cfg = load_accounts_config(path)
    print(f"OK: loaded {len(cfg.accounts)} accounts and {len(cfg.api_keys)} api keys")


def main() -> None:
    parser = argparse.ArgumentParser(description="Secure AI Email Gateway CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gen-key", help="Generate a random API key + Argon2 hash")
    chk = sub.add_parser("check-config", help="Validate YAML + environment")
    chk.add_argument("--config", default="config/accounts.yaml")

    args = parser.parse_args()
    if args.cmd == "gen-key":
        gen_key()
    elif args.cmd == "check-config":
        check_config(args.config)


if __name__ == "__main__":
    main()
