from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

from app.config import AccountConfig
from app.imap.client import imap_connection


def _clean_header(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("header_injection_detected")
    return value


def create_draft(account: AccountConfig, draft_folder: str, payload: dict[str, Any]) -> dict[str, Any]:
    msg = EmailMessage()
    msg["Subject"] = _clean_header(payload.get("subject", ""))
    if payload.get("to"):
        msg["To"] = ", ".join(_clean_header(v) for v in payload["to"])
    if payload.get("cc"):
        msg["Cc"] = ", ".join(_clean_header(v) for v in payload["cc"])
    if payload.get("bcc"):
        msg["Bcc"] = ", ".join(_clean_header(v) for v in payload["bcc"])

    msg_id = make_msgid()
    msg["Message-ID"] = msg_id

    text_body = payload.get("text_body")
    if not text_body:
        raise ValueError("text_body_required")
    html_body = payload.get("html_body")

    if html_body:
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
    else:
        msg.set_content(text_body)

    for attachment in payload.get("attachments", []):
        raw = base64.b64decode(attachment["content_base64"])
        maintype, subtype = attachment["mime_type"].split("/", 1)
        msg.add_attachment(raw, maintype=maintype, subtype=subtype, filename=attachment["filename"])

    raw = msg.as_bytes()
    with imap_connection(account) as client:
        client.select_folder(draft_folder)
        appended = client.append(draft_folder, raw, flags=[b"\\Draft"])

    uidvalidity = getattr(appended, "uidvalidity", None)
    uid = getattr(appended, "uid", None)
    draft_message_id = f"{uidvalidity}:{uid}" if uidvalidity and uid else f"local:{msg_id}"
    return {"draft_message_id": draft_message_id, "folder": draft_folder, "created_at": datetime.now(timezone.utc).isoformat()}
