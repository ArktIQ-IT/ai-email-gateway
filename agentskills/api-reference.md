# API Reference (ai-email-gateway)

Base URL example: `http://localhost:8000`

Auth header:

```text
Authorization: Bearer <GATEWAY_API_KEY>
Content-Type: application/json
```

## List accessible accounts

- `GET /v1/accounts`

## Start manual sync job

- `POST /v1/accounts/{account_id}/sync`

Example body:

```json
{
  "folders": ["INBOX", "Sent"],
  "since": "2026-01-01T00:00:00Z",
  "until": "2026-03-01T00:00:00Z",
  "include_subfolders": true,
  "limit_per_folder": 500
}
```

## Poll job status

- `GET /v1/jobs/{job_id}`

## List cached messages

- `POST /v1/accounts/{account_id}/messages:list`

Supported filters:
- `folders`
- `since`, `until`
- `senders`
- `recipients`
- `free_text`
- `direction` (`incoming`, `sent`, `unknown`, `all`)
- `limit`, `offset`
- `include_body`

Example body:

```json
{
  "since": "2026-03-01T00:00:00Z",
  "until": "2026-03-08T00:00:00Z",
  "senders": ["alice@company.com"],
  "recipients": [],
  "free_text": ["follow up", "proposal"],
  "direction": "incoming",
  "limit": 100,
  "offset": 0,
  "include_body": true
}
```

## Get one cached message

- `POST /v1/accounts/{account_id}/messages:get`

```json
{
  "id": "INBOX|12345|67890"
}
```

## Create a draft

- `POST /v1/accounts/{account_id}/drafts`

```json
{
  "to": ["a@example.com"],
  "cc": [],
  "bcc": [],
  "subject": "Re: Subject",
  "text_body": "Draft body text",
  "html_body": null,
  "attachments": []
}
```

## Canonical message id

- Format: `folder|uidvalidity|uid`
- Source: returned as `id` by `messages:list`
