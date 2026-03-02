from __future__ import annotations

import email
import hashlib
import json
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from typing import Any

from app.config import AccountConfig, Settings
from app.imap.client import imap_connection


SAFE_HEADERS = {"from", "to", "cc", "subject", "message-id", "date"}


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return repr(value)
    return str(value)


def _decode_header_safe(raw: str | None) -> str | None:
    if raw is None:
        return None
    return str(make_header(decode_header(raw)))


def _extract_text(msg: Message, max_chars: int = 5000) -> str:
    text_parts: list[str] = []
    html_fallback = None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
            if ctype == "text/plain":
                text_parts.append(body)
            elif ctype == "text/html" and html_fallback is None:
                html_fallback = body
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        body = payload.decode(charset, errors="replace")
        if msg.get_content_type() == "text/plain":
            text_parts.append(body)
        else:
            html_fallback = body
    content = "\n".join(text_parts).strip()
    if not content and html_fallback:
        # Very simple HTML to text fallback.
        content = " ".join(html_fallback.replace("<", " <").split())
    return content[:max_chars]


def list_messages(
    account_id: str,
    account: AccountConfig,
    folder: str,
    since: datetime,
    until: datetime,
    limit: int,
    output_format: str,
) -> dict[str, Any]:
    settings = Settings()
    with imap_connection(account) as client:
        client.select_folder(folder, readonly=True)
        query = ["SINCE", since.date(), "BEFORE", until.date()]
        uids = client.search(query)
        uids = sorted(uids)[-limit:]
        if not uids:
            return {"messages": [], "count": 0}
        fetch_fields = [b"ENVELOPE", b"INTERNALDATE", b"RFC822.SIZE", b"FLAGS", b"BODY.PEEK[HEADER]"]
        if output_format in {"text", "text+attachments-metadata", "raw"}:
            fetch_fields.append(b"BODY.PEEK[]")

        payload = client.fetch(uids, fetch_fields)
        messages = []
        for uid in uids:
            row = payload[uid]
            envelope = row.get(b"ENVELOPE")
            raw_header = row.get(b"BODY[HEADER]", b"")
            parsed_header = email.message_from_bytes(raw_header)
            item = {
                "uid": uid,
                "internal_date": _to_text(row.get(b"INTERNALDATE")),
                "size": row.get(b"RFC822.SIZE"),
                "flags": [_to_text(f) for f in row.get(b"FLAGS", [])],
                "headers": {
                    h: _decode_header_safe(parsed_header.get(h))
                    for h in SAFE_HEADERS
                    if parsed_header.get(h) is not None
                },
                "subject": _decode_header_safe(getattr(envelope, "subject", None) and envelope.subject.decode(errors="replace")),
            }
            if output_format == "raw" and settings.allow_raw:
                item["raw"] = row.get(b"BODY[]", b"").decode("utf-8", errors="replace")
            elif output_format in {"text", "text+attachments-metadata"}:
                msg = email.message_from_bytes(row.get(b"BODY[]", b""))
                item["text"] = _extract_text(msg)
                if output_format == "text+attachments-metadata":
                    attachments = []
                    for part in msg.walk():
                        if part.get_content_disposition() != "attachment":
                            continue
                        data = part.get_payload(decode=True) or b""
                        attachments.append(
                            {
                                "filename": part.get_filename(),
                                "mime_type": part.get_content_type(),
                                "size": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                            }
                        )
                    item["attachments"] = attachments
            messages.append(item)
    return {"messages": messages, "count": len(messages), "account_id": account_id, "folder": folder, "since": since.isoformat(), "until": until.isoformat()}
