# Secure AI e-mail gateway (FastAPI + IMAP)

A safer API gateway for AI-assisted email workflows: your AI agent can read mailbox context and create follow-up drafts over IMAP **without ever getting mailbox credentials or send-mail permissions**.

This service is designed for teams who want AI help with triage and follow-up preparation, while keeping final send control in a human inbox client.

## Security goals
- AI agents only get a gateway API key, never IMAP credentials.
- API keys are verified against Argon2 hashes from config.
- IMAP passwords are loaded from environment variables (`imap_password_env`), not from YAML.
- No SMTP integration and no send-email endpoint.
- Draft creation uses IMAP `APPEND` only (draft/write to mailbox folder, not send).
- Raw RFC822 output is disabled by default (`ALLOW_RAW=false`).
- Per-key auth rate limits and stricter job route limits.
- Job errors are sanitized to error class codes (no traceback/secret payloads).

## Security validation status (current codebase)
The current implementation meets the goals above:
- Auth and account scoping are enforced on all `/v1/*` data endpoints.
- IMAP access uses login with env-provided password values.
- There is no SMTP client usage and no route that sends mail.
- Draft route writes via IMAP `APPEND` into a mailbox folder; it does not transmit mail to recipients.
- Worker job failures persist sanitized error codes such as `job_failed:SomeError`.

Operational caveat:
- This project prevents API-triggered send in its own code path. Mailbox/provider-side automations or external tooling are outside this service boundary.

## Features
- `/v1/accounts` account discovery per API key allowlist.
- Cached message reads (`messages:list`, `messages:get`) from SQLite.
- Sync jobs (`/sync`) to ingest mailbox history from configured folders (and optional subfolders) into cache.
- Cache maintenance endpoints for deleting cached messages by ids or age/timespan.
- Draft creation via IMAP `APPEND` into Drafts folder.
- SQLite + SQLAlchemy persistence for jobs, locks, and message index table.

## Provider setup
### Domeneshop
Use regular IMAP TLS settings:
- host: `imap.domeneshop.no`
- port: `993`
- tls mode: `ssl`

### Proton Mail (Bridge only)
This gateway supports Proton through **Proton Mail Bridge** (local IMAP adapter):
1. Run Proton Bridge on host machine.
2. Create bridge mailbox credentials.
3. Point account config to bridge host/port and bridge credentials env var.

## Quickstart (non-docker)
```bash
python -m venv .venv
source .venv/bin/activate
mkdir -p data
pip install -r requirements.txt
cp .env.example .env
cp config/accounts.example.yaml config/accounts.yaml
# set referenced IMAP secrets in .env (or export in shell)
python -m app.cli check-config --config config/accounts.yaml
uvicorn app.main:app --reload
```

Open docs: `http://localhost:8000/docs`

## Docker / Compose
```bash
cp .env.example .env
cp config/accounts.example.yaml config/accounts.yaml
docker compose up --build
```

The compose file mounts config read-only and persists SQLite in `./data`.

## CLI
Generate API key + Argon2 hash:
```bash
python -m app.cli gen-key
```

Validate config and env references:
```bash
python -m app.cli check-config --config config/accounts.yaml
```

## API usage examples
Start manual sync job (ingest IMAP -> local cache):
```bash
curl -X POST "http://localhost:8000/v1/accounts/domeneshop-main/sync" \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"folders":["INBOX","Sent"],"since":"2026-03-01T00:00:00Z","until":"2026-03-07T00:00:00Z","include_subfolders":true,"limit_per_folder":500}'
```

Poll job:
```bash
curl "http://localhost:8000/v1/jobs/<job_id>" -H "Authorization: Bearer <API_KEY>"
```

Check sync status:
```bash
curl "http://localhost:8000/v1/accounts/domeneshop-main/sync:status" \
  -H "Authorization: Bearer <API_KEY>"
```

List cached messages:
```bash
curl -X POST "http://localhost:8000/v1/accounts/domeneshop-main/messages:list" \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"since":"2026-01-01T00:00:00Z","until":"2026-03-01T00:00:00Z","senders":["boss@company.com"],"free_text":["invoice","project"],"limit":50}'
```

`messages:list` returns `id` as an IMAP-derived cache key: `folder|uidvalidity|uid`.
Use that `id` for `messages:get` and `sync:delete-by-ids`.

Get one cached message:
```bash
curl -X POST "http://localhost:8000/v1/accounts/domeneshop-main/messages:get" \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"id":"INBOX|12345|67890"}'
```

Delete cached messages by IDs:
```bash
curl -X POST "http://localhost:8000/v1/accounts/domeneshop-main/sync:delete-by-ids" \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"ids":["INBOX|12345|67890","Sent|12345|222"]}'
```

Create draft:
```bash
curl -X POST "http://localhost:8000/v1/accounts/domeneshop-main/drafts" \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"to":["a@example.com"],"subject":"Hi","text_body":"Draft only"}'
```

## Operational notes
- Current worker model is an in-process asyncio queue.
- Run uvicorn with `--workers 1` unless you implement shared DB-backed worker claiming.
- Automatic sync scheduler is built-in and runs every minute to enqueue due account sync jobs.
- On startup, the scheduler always enqueues an immediate sync pass for enabled accounts.
- For historical context, run a manual sync for the timespan you want your AI to use as starting context.
  - Example: if you want the AI to understand prior conversation history before suggesting follow-ups, call `POST /v1/accounts/{account_id}/sync` with explicit `since`/`until` covering that historical period.
- Per-account auto-sync behavior is configured in `config/accounts.yaml`:
  - `auto_sync_enabled` (default `true`)
  - `auto_sync_interval_minutes` (default `15`)
  - `auto_sync_lookback_minutes` (default `interval * 2`, e.g. `30` when interval is `15`)
  - `auto_sync_include_subfolders` (default `true`)
  - `auto_sync_limit_per_folder` (default `500`)
- On-demand sync is still available via `POST /v1/accounts/{account_id}/sync`.
- On-demand sync uses explicit `since`/`until` for date range control.
- Sync only adds/updates cache entries; it never deletes mailbox messages.

## Developer notes (secure updates)
- Keep secrets out of source control: never commit `.env`, plaintext keys, or mailbox passwords.
- Preserve auth boundaries: every new endpoint must require API-key auth and enforce account allowlists.
- Treat cache as sensitive data: avoid logging message body, headers, or credentials in errors and debug output.
- Keep sync additive by default: only dedicated delete endpoints may remove cached data.
- Use canonical message IDs (`folder|uidvalidity|uid`) in API contracts; do not expose DB row IDs.
- Keep docs and behavior aligned: update `/docs` descriptions, examples, and this README with any contract changes.
- Validate before merge: run `python -m app.cli check-config --config config/accounts.yaml` and `python -m compileall app`.
- If a key is exposed (terminal/chat/log), rotate immediately: generate a new key, update hash in config, revoke old key.
- Dependency locking for deterministic Docker builds:
  - Keep top-level dependencies in `requirements.in` (human-maintained), and generate a fully pinned `requirements.txt` (machine-generated).
  - Use `pip-tools` to compile with hashes: `pip-compile --generate-hashes --output-file requirements.txt requirements.in`.
  - Audit before installing/upgrading:
    - Review lockfile diffs (`git diff requirements.txt`) for unexpected package/version jumps.
    - Preview resolver actions with pip without changing env: `python -m pip install --dry-run --require-hashes -r requirements.txt`.
    - Run dependency integrity checks on current env: `python -m pip check`.
    - (Recommended) run vulnerability scan on lockfile: `pip-audit -r requirements.txt`.
  - Build/install with hashes enforced (`pip install --require-hashes -r requirements.txt`) so Docker builds are reproducible and tamper-evident.
  - Understand install vs sync:
    - `pip install -r requirements.txt` updates/adds required packages but can leave extra, previously installed packages in the environment.
    - `pip-sync requirements.txt` (from `pip-tools`) makes the environment match the lockfile exactly by removing packages not in `requirements.txt`.
  - In Docker, install from the locked file only; do not install from `requirements.in` during image build.
