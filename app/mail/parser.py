"""Email parsing utilities."""

from __future__ import annotations

import email
import hashlib
import re
from datetime import datetime
from email.header import decode_header, make_header
from email.message import Message
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime, parseaddr
from pathlib import Path

from bs4 import BeautifulSoup

from app.core.time_utils import to_local_naive
from app.db.models import ParsedMail


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _normalize_subject_for_thread(subject: str) -> str:
    """Strip reply and forward prefixes for thread grouping."""

    normalized = subject.strip()
    while True:
        updated = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)", "", normalized, flags=re.IGNORECASE)
        if updated == normalized:
            break
        normalized = updated.strip()
    return normalized or subject.strip()


def _extract_message_ids(header_value: str | None) -> list[str]:
    """Extract message-id tokens from reply-chain headers."""

    if not header_value:
        return []
    return [item.strip() for item in re.findall(r"<[^>]+>", header_value)]


def _decode_payload(payload: bytes, charset: str | None) -> str:
    effective_charset = charset or "utf-8"
    try:
        return payload.decode(effective_charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _extract_text_part(message: Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            content_type = part.get_content_type()
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            decoded = _decode_payload(payload, part.get_content_charset())
            if content_type == "text/plain":
                plain_parts.append(decoded)
            elif content_type == "text/html":
                html_parts.append(decoded)
    else:
        payload = message.get_payload(decode=True)
        decoded = _decode_payload(payload, message.get_content_charset()) if payload else ""
        if message.get_content_type() == "text/html":
            html_parts.append(decoded)
        else:
            plain_parts.append(decoded)

    plain_text = _normalize_text("\n".join(plain_parts))
    if plain_text:
        return plain_text

    html_fragments: list[str] = []
    for part in html_parts:
        soup = BeautifulSoup(part, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        html_fragments.append(soup.get_text("\n"))
    html_text = _normalize_text("\n".join(html_fragments))
    return html_text


def _sanitize_attachment_name(name: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip().strip(".")
    return sanitized or "attachment"


def _resolve_unique_attachment_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(2, 1000):
        versioned = directory / f"{stem} ({index}){suffix}"
        if not versioned.exists():
            return versioned
    digest = hashlib.sha256(filename.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return directory / f"{stem}-{digest}{suffix}"


def _extract_attachments(
    message: Message,
    message_id: str,
    attachment_storage_root: Path | None,
    attachment_relative_root: Path | None,
) -> tuple[list[str], list[str]]:
    attachment_names: list[str] = []
    attachment_paths: list[str] = []
    attachment_directory: Path | None = None

    attachment_index = 1
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        if "attachment" not in disposition and "inline" not in disposition:
            continue

        raw_filename = _decode_header_value(part.get_filename())
        payload = part.get_payload(decode=True)
        if not raw_filename and payload is None:
            continue

        filename = raw_filename or f"attachment-{attachment_index:02d}.bin"
        attachment_index += 1
        attachment_names.append(filename)

        if payload is None:
            continue

        if attachment_directory is None and attachment_storage_root is not None:
            attachment_directory = attachment_storage_root / hashlib.sha256(
                message_id.encode("utf-8", errors="ignore")
            ).hexdigest()[:24]
            attachment_directory.mkdir(parents=True, exist_ok=True)

        if attachment_directory is None:
            continue

        safe_name = _sanitize_attachment_name(filename)
        target_path = _resolve_unique_attachment_path(attachment_directory, safe_name)
        target_path.write_bytes(payload)
        if attachment_relative_root is not None:
            attachment_paths.append(target_path.relative_to(attachment_relative_root).as_posix())
        else:
            attachment_paths.append(str(target_path))

    return attachment_names, attachment_paths


def parse_email_bytes(
    raw_bytes: bytes,
    preview_max_chars: int = 4000,
    fallback_message_id: str | None = None,
    received_at_override: datetime | None = None,
    attachment_storage_root: Path | None = None,
    attachment_relative_root: Path | None = None,
) -> ParsedMail:
    """Parse raw RFC822 bytes into a normalized structure."""

    message = email.message_from_bytes(raw_bytes, policy=default)
    stable_fallback_id = hashlib.sha256(raw_bytes).hexdigest()[:24]
    message_id = _decode_header_value(message.get("Message-ID")) or fallback_message_id or f"missing-{stable_fallback_id}"
    subject = _decode_header_value(message.get("Subject")) or "(제목 없음)"
    normalized_subject = _normalize_subject_for_thread(subject)
    in_reply_to_candidates = _extract_message_ids(_decode_header_value(message.get("In-Reply-To")))
    references = _extract_message_ids(_decode_header_value(message.get("References")))
    in_reply_to = in_reply_to_candidates[-1] if in_reply_to_candidates else None

    sender_name, sender_email = parseaddr(_decode_header_value(message.get("From")))
    to_list = [address for _, address in getaddresses(message.get_all("To", [])) if address]
    cc_list = [address for _, address in getaddresses(message.get_all("Cc", [])) if address]

    received_at: datetime | None = received_at_override
    if received_at is None and message.get("Date"):
        try:
            parsed = parsedate_to_datetime(message.get("Date"))
            received_at = to_local_naive(parsed)
        except Exception:  # noqa: BLE001
            received_at = None

    body_text = _extract_text_part(message)
    raw_preview = body_text[:preview_max_chars]
    attachment_names, attachment_paths = _extract_attachments(
        message,
        message_id=message_id,
        attachment_storage_root=attachment_storage_root,
        attachment_relative_root=attachment_relative_root,
    )

    return ParsedMail(
        message_id=message_id,
        subject=subject,
        normalized_subject=normalized_subject,
        thread_key=message_id.lower(),
        sender_name=sender_name,
        sender_email=sender_email,
        to_list=to_list,
        cc_list=cc_list,
        received_at=received_at,
        body_text=body_text,
        raw_preview=raw_preview,
        in_reply_to=in_reply_to,
        references=references,
        attachment_names=[name for name in attachment_names if name],
        attachment_paths=[path for path in attachment_paths if path],
    )
