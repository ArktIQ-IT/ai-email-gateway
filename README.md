# Secure AI Email Gateway (FastAPI + IMAP)

A secure API gateway that lets AI systems read mailbox data and create drafts over IMAP **without exposing IMAP credentials**.

## Threat model / security goals
- AI agents only get a gateway API key, never IMAP credentials.
- API keys are stored as Argon2 hashes in YAML.
- IMAP passwords are loaded only from environment variables.
- No SMTP integration and no send-email endpoint.
- Raw RFC822 output is disabled by default (`ALLOW_RAW=false`).
- Per-key rate limits + stricter job/read limits.
- Job errors are sanitized and should never leak secrets.

## Features
- `/v1/accounts` account discovery per API key allowlist.
- Job-based message reads (`messages:list`, `messages:get`) with polling.
- Single-flight behavior: one running read per account+folder.
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
pip install -r requirements.txt
cp .env.example .env
cp config/accounts.example.yaml config/accounts.yaml
# set referenced IMAP secrets, e.g.:
export DOMENESHOP_IMAP_PASSWORD=...
export PROTON_BRIDGE_PASSWORD=...
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
Start message list job:
```bash
curl -X POST "http://localhost:8000/v1/accounts/domeneshop-main/messages:list" \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"folder":"INBOX","sync_mode":"incremental","output_format":"text","limit":50}'
```

Poll job:
```bash
curl "http://localhost:8000/v1/jobs/<job_id>" -H "Authorization: Bearer <API_KEY>"
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
- No background cron sync. Reads happen only when API calls trigger jobs.
