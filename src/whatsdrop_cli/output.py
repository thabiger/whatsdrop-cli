"""Render archive records as NDJSON, text or CSV."""

from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime, tzinfo

BASE64_PREVIEW_RE = re.compile(r"^[A-Za-z0-9+/=\s]{200,}$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

MEDIA_TYPES = {
    "media",
    "image",
    "video",
    "audio",
    "ptt",
    "document",
    "sticker",
    "gif",
    "vcard",
    "location",
    "live_location",
    "poll_creation",
}


def _timestamp(record: dict):
    value = record.get("ts") or record.get("sortTs") or record.get("capturedAt")
    return value if isinstance(value, (int, float)) else None


def _moment(record: dict, tz: tzinfo) -> datetime | None:
    value = _timestamp(record)
    return datetime.fromtimestamp(value / 1000, tz=tz) if value else None


def _text(record: dict) -> str:
    text = record.get("text") or ""
    if record.get("type") in MEDIA_TYPES and text and BASE64_PREVIEW_RE.match(text):
        return "[preview omitted]"
    return text


def _body(record: dict) -> str:
    text = _text(record)
    kind = record.get("type") or ""
    if kind == "revoked":
        return "[deleted message]"
    if kind in MEDIA_TYPES:
        return f"[{kind}] {text}".strip()
    return text or "[no text]"


def record_moment(record: dict, tz: tzinfo) -> datetime | None:
    return _moment(record, tz)


def record_body(record: dict) -> str:
    return _body(record)


def escape_text(value: object) -> str:
    """Escape control characters for safe terminal/text output."""
    text = str(value)
    text = text.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    return CONTROL_RE.sub("\ufffd", text)


def safe_cell(value: object) -> str:
    """Replace control characters in table cells."""
    return CONTROL_RE.sub(" ", str(value))


def _csv_safe(value: object) -> str:
    """Prevent spreadsheet formula injection in CSV cells."""
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def render_ndjson(records: list[dict]) -> list[str]:
    return [json.dumps(record, ensure_ascii=False) for record in records]


def render_text(records: list[dict], tz: tzinfo) -> list[str]:
    lines: list[str] = []
    for record in records:
        moment = _moment(record, tz)
        stamp = moment.strftime("%Y-%m-%d %H:%M") if moment else "????-??-?? ??:??"
        direction = record.get("direction")
        glyph = "→" if direction == "out" else "←" if direction == "in" else "·"
        sender = "You" if direction == "out" else (record.get("senderName") or "Unknown")
        chat = record.get("chatName") or "unknown"
        lines.append(
            f"{stamp} {glyph} {escape_text(chat)} / {escape_text(sender)}: "
            f"{escape_text(_body(record))}"
        )
    return lines


def render_csv(records: list[dict], tz: tzinfo) -> list[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["msgId", "ts", "chatName", "chatPhone", "senderName", "direction", "type", "text"]
    )
    for record in records:
        moment = _moment(record, tz)
        writer.writerow(
            [
                _csv_safe(record.get("msgId") or ""),
                _csv_safe(moment.isoformat() if moment else ""),
                _csv_safe(record.get("chatName") or ""),
                _csv_safe(record.get("chatPhone") or ""),
                _csv_safe(record.get("senderName") or ""),
                _csv_safe(record.get("direction") or ""),
                _csv_safe(record.get("type") or ""),
                _csv_safe(_text(record)),
            ]
        )
    return buffer.getvalue().splitlines()


def render(records: list[dict], fmt: str, tz: tzinfo) -> list[str]:
    if fmt == "ndjson":
        return render_ndjson(records)
    if fmt == "text":
        return render_text(records, tz)
    if fmt == "csv":
        return render_csv(records, tz)
    raise ValueError(f"unknown format: {fmt}")


def write_lines(lines: list[str], out_path: str | None = None) -> None:
    if out_path:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        with os.fdopen(
            os.open(out_path, flags, 0o600), "w", encoding="utf-8", newline="\n"
        ) as handle:
            os.chmod(out_path, 0o600)
            for line in lines:
                handle.write(line + "\n")
        return
    for line in lines:
        print(line)
