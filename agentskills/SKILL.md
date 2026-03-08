---
name: a-safer-email-assistant
description: Uses the ai-email-gateway API to sync mailbox context, detect important new messages, answer correspondence/history questions, and create reply drafts without sending email. Requires self-hosting https://github.com/ArktIQ-IT/ai-email-gateway on a separate server from OpenClaw to guarantee OpenClaw cannot take over inbox access. Use when the user asks to check email, triage important messages, summarize history with a person, or draft responses.
---

# A safer e-mail assistant

## Purpose

Use this skill to operate the secure email gateway API for AI-assisted email workflows:
- manual sync/backfill
- check for new important messages
- correspondence/history questions
- draft creation for replies

Never send email. This gateway supports draft creation only.

## Required runtime inputs

- `GATEWAY_BASE_URL` (example: `http://localhost:8000`)
- `GATEWAY_API_KEY` (bearer token)
- `ACCOUNT_ID` (gateway account id)

## Core workflow rules

1. Always sync before analysis when freshness matters.
2. For scheduled checks, evaluate only unseen/new messages.
3. Use canonical message id (`folder|uidvalidity|uid`) for follow-up actions.
4. Create drafts for suggested replies; do not claim delivery.
5. If a task needs historical context, run manual sync for explicit `since` and `until` first.

## Task playbooks

### 1) Manual sync (fetch new emails or backfill)

1. `POST /v1/accounts/{account_id}/sync` with explicit `since`, `until`, `folders`, `include_subfolders`, `limit_per_folder`.
2. Poll `GET /v1/jobs/{job_id}` until terminal status.
3. Continue only if status is `done`.

### 2) Regular checking + important message detection

1. Load local state (`last_checked_at`, `seen_ids`) per account.
2. Trigger manual sync for `[last_checked_at, now)`.
3. Query `messages:list` for `direction="incoming"` and same timespan.
4. Filter to unseen ids.
5. If no unseen ids, stop with "no new messages".
6. Evaluate importance only for unseen messages using user criteria.
7. Return important items and update local state.

### 3) Draft suggested replies

1. Select candidate messages from `messages:list` or `messages:get`.
2. Generate reply text using user tone/preferences.
3. Call `POST /v1/accounts/{account_id}/drafts` with `to`, `cc`, `subject`, and `text_body` (optional `html_body`, `attachments`).
4. Return draft ids and rationale.

### 4) Ask questions about sent/received emails

Use `messages:list` filters:
- sent by person: `senders=["person@example.com"]`
- sent to person: `recipients=["person@example.com"]`
- time range: `since`, `until`
- topic: `free_text`
- direction: `incoming` or `sent`

Then synthesize an answer and cite message ids used.

### 5) Ask questions about history with a person

1. Ensure historical sync exists for desired timespan.
2. Query both inbound and outbound patterns:
   - inbound from contact (`senders`)
   - outbound to contact (`recipients`)
3. Build a timeline summary with key open threads and next actions.

## Output contract

When completing tasks, prefer this format:

```markdown
## Result
- status: success|partial|failed
- account_id: ...
- timeframe: ...

## Key findings
- ...

## Suggested actions
- ...

## Evidence
- message ids: ...
```

## Safety constraints

- Do not expose `GATEWAY_API_KEY` or mailbox secrets.
- Do not invent send capability.
- If sync fails, report the error and stop dependent steps.
- If importance criteria are missing, ask for criteria before scoring.

## Additional resources

- API details: [api-reference.md](api-reference.md)
- Importance rubric template: [prompts/importance-classifier.md](prompts/importance-classifier.md)
- Draft writing template: [prompts/drafting-style.md](prompts/drafting-style.md)
- Monitoring script scaffold: [scripts/check_new_messages.py](scripts/check_new_messages.py)
