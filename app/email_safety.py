from __future__ import annotations

import re
from typing import Any

# Easy to extend: add a new language code with the same keys.
LANGUAGE_INJECTION_TERMS: dict[str, dict[str, list[str]]] = {
    "en": {
        "prompt_injection_phrase": [r"\b(ignore|disregard|override)\b.{0,40}\b(previous|prior|above)\b"],
        "credential_exfiltration": [r"\b(api[_ -]?key|password|secret|token|private key)\b"],
        "jailbreak_attempt": [r"\b(system prompt|developer message|hidden instructions)\b"],
        "encoded_payload": [r"\b(base64|rot13|hex decode|decode this)\b"],
        "code_execution_lure": [r"\b(run this|execute this|shell command|powershell|curl\s+http)\b"],
    },
    "no": {
        "prompt_injection_phrase": [r"\b(ignorer|overstyr|se bort fra)\b.{0,40}\b(tidligere|forrige|ovenfor)\b"],
        "credential_exfiltration": [r"\b(api[_ -]?nøkkel|passord|hemmelighet|token|privat nøkkel)\b"],
        "jailbreak_attempt": [r"\b(systemprompt|utviklermelding|skjulte instruksjoner)\b"],
        "encoded_payload": [r"\b(base64|rot13|hex[- ]?dekod|dekod dette)\b"],
        "code_execution_lure": [r"\b(kjør dette|utfør dette|shell-kommando|powershell|curl\s+http)\b"],
    },
    "de": {
        "prompt_injection_phrase": [r"\b(ignoriere|missachte|überschreibe)\b.{0,40}\b(vorherige|frühere|oben)\b"],
        "credential_exfiltration": [r"\b(api[_ -]?schlüssel|passwort|geheimnis|token|privater schlüssel)\b"],
        "jailbreak_attempt": [r"\b(systemprompt|entwicklernachricht|versteckte anweisungen)\b"],
        "encoded_payload": [r"\b(base64|rot13|hex dekodieren|dekodiere dies)\b"],
        "code_execution_lure": [r"\b(führe dies aus|ausführen|shell-befehl|powershell|curl\s+http)\b"],
    },
    "fr": {
        "prompt_injection_phrase": [r"\b(ignore|ignorez|outrepasse)\b.{0,40}\b(précédent|précédente|ci-dessus)\b"],
        "credential_exfiltration": [r"\b(clé api|mot de passe|secret|jeton|clé privée)\b"],
        "jailbreak_attempt": [r"\b(prompt système|message développeur|instructions cachées)\b"],
        "encoded_payload": [r"\b(base64|rot13|décodage hex|décode ceci)\b"],
        "code_execution_lure": [r"\b(exécute ceci|lance ceci|commande shell|powershell|curl\s+http)\b"],
    },
}

DEFAULT_INJECTION_LANGUAGES = ("en", "no", "de", "fr")

_LONG_UNBROKEN = re.compile(r"[A-Za-z0-9+/=]{180,}")
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _build_injection_patterns(languages: tuple[str, ...] = DEFAULT_INJECTION_LANGUAGES) -> list[tuple[str, re.Pattern[str]]]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for language in languages:
        entries = LANGUAGE_INJECTION_TERMS.get(language, {})
        for finding, regexes in entries.items():
            for regex in regexes:
                patterns.append((finding, re.compile(regex, re.IGNORECASE)))
    return patterns


INJECTION_PATTERNS = _build_injection_patterns()


def clean_email_text(text: str | None, max_chars: int = 4000) -> str:
    if not text:
        return ""
    cleaned = text
    cleaned = _CODE_FENCE.sub(" ", cleaned)
    cleaned = _HTML_TAG.sub(" ", cleaned)
    cleaned = "\n".join(line for line in cleaned.splitlines() if not _LONG_UNBROKEN.search(line))
    cleaned = "\n".join(line for line in cleaned.splitlines() if not line.strip().startswith((">", "|")))
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned[:max_chars]


def analyze_prompt_injection(subject: str | None, body: str | None) -> dict[str, Any]:
    content = f"{subject or ''}\n{body or ''}"
    findings: list[str] = []
    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(content) and name not in findings:
            findings.append(name)
    if _LONG_UNBROKEN.search(content):
        findings.append("long_encoded_segment")
    score = min(len(findings), 5)
    return {
        "score": score,
        "is_suspicious": score > 0,
        "findings": findings,
        "languages": list(DEFAULT_INJECTION_LANGUAGES),
    }


def normalize_subject_for_thread(subject: str | None) -> str | None:
    if not subject:
        return None
    current = subject.strip().lower()
    while True:
        updated = re.sub(r"^(re|fwd?|sv)\s*:\s*", "", current, count=1, flags=re.IGNORECASE)
        if updated == current:
            break
        current = updated.strip()
    current = _WHITESPACE.sub(" ", current)
    return current or None


def normalize_message_id(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip().strip("<>")
    return stripped.lower() or None


def build_thread_key(message_id: str | None, in_reply_to: str | None, references: str | None, subject: str | None) -> str:
    refs = [normalize_message_id(v) for v in re.findall(r"<([^>]+)>", references or "") if normalize_message_id(v)]
    root_ref = refs[0] if refs else None
    in_reply = normalize_message_id(in_reply_to)
    msg_id = normalize_message_id(message_id)
    subj = normalize_subject_for_thread(subject)

    if root_ref:
        return f"ref:{root_ref}"
    if in_reply:
        return f"reply:{in_reply}"
    if msg_id:
        return f"msg:{msg_id}"
    return f"subj:{subj or 'unknown'}"
