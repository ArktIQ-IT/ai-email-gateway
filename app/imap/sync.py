from __future__ import annotations

import email
import hashlib
import json
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AccountConfig, Settings
from app.imap.client import imap_connection
from app.models import MessageIndex


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


def _normalize_dt(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _decode_address_header(msg: Message, key: str) -> str | None:
    raw = msg.get(key)
    if raw is None:
        return None
    return _decode_header_safe(raw)


def _infer_direction(account: AccountConfig, from_value: str | None) -> str:
    if not from_value:
        return "unknown"
    if account.imap_username.lower() in from_value.lower():
        return "sent"
    return "incoming"


def _folder_candidates(account: AccountConfig, include_subfolders: bool, roots: list[str] | None = None) -> list[str]:
    seen: set[str] = set()
    folders: list[str] = []

    for folder in (roots or account.folders_read):
        if folder not in seen:
            folders.append(folder)
            seen.add(folder)

    if not include_subfolders:
        return folders

    expanded = list(folders)
    with imap_connection(account) as client:
        listed = client.list_folders()
        for _, delimiter, name in listed:
            folder_name = _to_text(name) or ""

            for root in folders:
                if folder_name == root:
                    if folder_name not in seen:
                        expanded.append(folder_name)
                        seen.add(folder_name)
                    break
                if delimiter:
                    prefix = f"{root}{_to_text(delimiter) or ''}"
                    if folder_name.startswith(prefix):
                        if folder_name not in seen:
                            expanded.append(folder_name)
                            seen.add(folder_name)
                        break
                if folder_name.startswith(f"{root}/") or folder_name.startswith(f"{root}."):
                    if folder_name not in seen:
                        expanded.append(folder_name)
                        seen.add(folder_name)
                    break
    return expanded


def sync_messages_to_cache(
    db: Session,
    account_id: str,
    account: AccountConfig,
    folders: list[str] | None,
    since: datetime,
    until: datetime,
    include_subfolders: bool = True,
    limit_per_folder: int = 500,
) -> dict[str, Any]:
    roots = [value for value in (folders or []) if value] or None
    target_folders = _folder_candidates(account, include_subfolders=include_subfolders, roots=roots)
    synced = 0
    updated = 0
    scanned_folders: list[str] = []

    with imap_connection(account) as client:
        for folder in target_folders:
            try:
                selected = client.select_folder(folder, readonly=True)
            except Exception:
                continue

            scanned_folders.append(folder)
            uidvalidity_raw = None
            if isinstance(selected, dict):
                uidvalidity_raw = selected.get(b"UIDVALIDITY") or selected.get("UIDVALIDITY")
            uidvalidity = int(uidvalidity_raw or 0)
            if uidvalidity <= 0:
                continue

            query = ["SINCE", since.date(), "BEFORE", until.date()]
            uids = sorted(client.search(query))[-limit_per_folder:]
            if not uids:
                continue

            fetch_fields = [b"INTERNALDATE", b"RFC822.SIZE", b"FLAGS", b"BODY.PEEK[HEADER]", b"BODY.PEEK[]"]
            payload = client.fetch(uids, fetch_fields)

            for uid in uids:
                row = payload.get(uid, {})
                raw_header = row.get(b"BODY[HEADER]", b"")
                parsed_header = email.message_from_bytes(raw_header)
                full_msg = email.message_from_bytes(row.get(b"BODY[]", b""))

                from_value = _decode_address_header(parsed_header, "From")
                to_value = _decode_address_header(parsed_header, "To")
                cc_value = _decode_address_header(parsed_header, "Cc")
                subject_value = _decode_address_header(parsed_header, "Subject")
                body_text = _extract_text(full_msg, max_chars=12000)
                internal_date = _normalize_dt(row.get(b"INTERNALDATE"))
                message_id_header = _decode_address_header(parsed_header, "Message-ID")

                existing = db.scalar(
                    select(MessageIndex).where(
                        MessageIndex.account_id == account_id,
                        MessageIndex.folder == folder,
                        MessageIndex.uidvalidity == uidvalidity,
                        MessageIndex.uid == int(uid),
                    )
                )

                if existing:
                    target = existing
                    updated += 1
                else:
                    target = MessageIndex(
                        account_id=account_id,
                        folder=folder,
                        uidvalidity=uidvalidity,
                        uid=int(uid),
                    )
                    db.add(target)

                target.internal_date = internal_date
                target.from_ = from_value
                target.to = to_value
                target.cc = cc_value
                target.subject = subject_value
                target.message_id_header = message_id_header
                target.size = row.get(b"RFC822.SIZE")
                target.flags = json.dumps([_to_text(v) for v in row.get(b"FLAGS", [])])
                target.body_text = body_text
                target.direction = _infer_direction(account, from_value)
                synced += 1

            db.commit()

    return {
        "account_id": account_id,
        "folders_scanned": scanned_folders,
        "messages_processed": synced,
        "messages_updated": updated,
        "since": since.isoformat(),
        "until": until.isoformat(),
    }
