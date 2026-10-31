"""Group archive records into per-chat conversation chunks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import tzinfo

from .chats import ChatEntry
from .config import Window
from .fetch import effective_ts
from .output import escape_text, record_body, record_moment, safe_cell


@dataclass(frozen=True)
class ChatMeta:
    hash: str | None
    id: str | None
    name: str
    phone: str | None
    pushname: str | None
    is_group: bool

    @property
    def key(self) -> str:
        if self.hash:
            return self.hash
        if self.id:
            return self.id
        return f"name:{self.name.casefold()}"

    def as_dict(self) -> dict:
        return {
            "hash": self.hash,
            "id": self.id,
            "name": self.name,
            "phone": self.phone,
            "pushname": self.pushname,
            "isGroup": self.is_group,
        }


@dataclass
class ChatThread:
    meta: ChatMeta
    messages: list[dict] = field(default_factory=list)
    last_ts: int = 0


@dataclass
class ContextChunk:
    chunk_id: str
    meta: ChatMeta
    window: Window
    context_before_ms: int
    part: int
    parts: int
    oversized: bool
    messages: list[dict]

    def as_dict(self) -> dict:
        return {
            "chunkVersion": 1,
            "chunkId": self.chunk_id,
            "chat": self.meta.as_dict(),
            "window": {
                "start": self.window.start_ms,
                "end": self.window.end_ms,
                "startIso": self.window.start.isoformat(),
                "endIso": self.window.end.isoformat(),
            },
            "contextBeforeMs": self.context_before_ms,
            "part": self.part,
            "parts": self.parts,
            "oversized": self.oversized,
            "messages": self.messages,
        }


def _entry_maps(selected: list[ChatEntry]) -> tuple[dict[str, ChatEntry], dict[str, ChatEntry]]:
    by_id = {entry.chat_id: entry for entry in selected if entry.chat_id}
    counts: dict[str, int] = {}
    for entry in selected:
        name = entry.chat_name.casefold()
        counts[name] = counts.get(name, 0) + 1
    by_name = {
        entry.chat_name.casefold(): entry
        for entry in selected
        if entry.chat_name and counts[entry.chat_name.casefold()] == 1
    }
    return by_id, by_name


def _entry_meta(entry: ChatEntry) -> ChatMeta:
    return ChatMeta(
        hash=entry.hash or None,
        id=entry.chat_id or None,
        name=entry.chat_name or "unknown",
        phone=entry.chat_phone or None,
        pushname=entry.chat_pushname or None,
        is_group=entry.is_group,
    )


CONTEXT_MEDIA_TYPES = {"image", "sticker", "gif", "media"}


def is_media_record(record: dict) -> bool:
    return record.get("type") in CONTEXT_MEDIA_TYPES


def attach_media_exports(threads: list[ChatThread], entries: list[dict]) -> None:
    """Attach export results to media messages in the given threads."""
    by_id = {entry.get("msgId"): entry for entry in entries if entry.get("msgId")}
    for thread in threads:
        for message in thread.messages:
            if not is_media_record(message):
                continue
            entry = by_id.get(message.get("msgId"))
            if entry is None:
                message["mediaExport"] = {
                    "status": "skipped",
                    "file": None,
                    "error": "not considered for export",
                }
                continue
            message["mediaExport"] = {
                "status": entry.get("status") or "skipped",
                "file": entry.get("file"),
                "error": entry.get("error"),
            }


def group_records(
    records: list[dict], selected: list[ChatEntry], window: Window
) -> list[ChatThread]:
    """Group records by chat, keep only chats with messages in the window."""
    by_id, by_name = _entry_maps(selected)
    threads: dict[str, ChatThread] = {}

    for record in records:
        ts = effective_ts(record)
        if ts is None:
            continue

        raw_chat_id = record.get("chatId")
        chat_id = raw_chat_id if isinstance(raw_chat_id, str) and raw_chat_id else None
        name = str(record.get("chatName") or "").strip()

        entry = by_id.get(chat_id) if chat_id else None
        if entry is None and chat_id is None and name:
            entry = by_name.get(name.casefold())

        if entry is not None:
            key = entry.chat_id
            meta = _entry_meta(entry)
        else:
            label = name or "unknown"
            key = chat_id or f"name:{label.casefold()}"
            meta = ChatMeta(
                hash=None,
                id=chat_id,
                name=label,
                phone=str(record.get("chatPhone") or "") or None,
                pushname=str(record.get("chatPushname") or "") or None,
                is_group=bool(record.get("isGroup")) or bool(chat_id and chat_id.endswith("@g.us")),
            )

        thread = threads.get(key)
        if thread is None:
            thread = ChatThread(meta=meta)
            threads[key] = thread

        message = dict(record)
        message["inWindow"] = bool(window.start_ms <= ts < window.end_ms)
        thread.messages.append(message)

    result: list[ChatThread] = []
    for thread in threads.values():
        thread.messages.sort(key=lambda item: (effective_ts(item) or 0, item.get("msgId") or ""))
        in_window_ts = [effective_ts(item) for item in thread.messages if item.get("inWindow")]
        if not in_window_ts:
            continue
        thread.last_ts = int(max(in_window_ts))
        result.append(thread)

    result.sort(key=lambda item: (-item.last_ts, item.meta.key))
    return result


def _escape_inline(value: object) -> str:
    return escape_text(value)


def _safe_header(value: object) -> str:
    return safe_cell(str(value).replace("|", "/"))


def _quote_path(path: object) -> str:
    return json.dumps(str(path), ensure_ascii=False)


def _media_annotation(record: dict) -> str:
    export = record.get("mediaExport")
    if not isinstance(export, dict):
        return ""
    file = export.get("file")
    if file:
        return f" (file: {_quote_path(file)})"

    media = record.get("media") if isinstance(record.get("media"), dict) else {}
    status = str(media.get("status") or "")
    if status and status != "stored":
        return f" (media: {status})"
    if export.get("status") == "failed":
        return " (media: export failed)"
    if export.get("status") == "skipped":
        if not status:
            return " (media: unavailable)"
        return " (media: not exported)"
    return ""


def _message_line(record: dict, tz: tzinfo, context: bool) -> str:
    moment = record_moment(record, tz)
    stamp = moment.strftime("%Y-%m-%d %H:%M") if moment else "????-??-?? ??:??"
    direction = record.get("direction")
    glyph = "→" if direction == "out" else "←" if direction == "in" else "·"
    sender = "You" if direction == "out" else (record.get("senderName") or "Unknown")
    marker = "[context] " if context else ""
    body = _escape_inline(record_body(record))
    return f"[{stamp}] {marker}{glyph} {_escape_inline(sender)}: {body}{_media_annotation(record)}"


def _duration_label(milliseconds: int) -> str:
    seconds = max(milliseconds, 0) // 1000
    for unit_seconds, suffix in ((604800, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if seconds and seconds % unit_seconds == 0:
            return f"{seconds // unit_seconds}{suffix}"
    return f"{seconds}s"


def _window_label(window: Window, tz: tzinfo) -> str:
    start = window.start.astimezone(tz)
    end = window.end.astimezone(tz)
    zone = start.tzname() or start.strftime("%z")
    if start.date() == end.date():
        span = f"{start:%Y-%m-%d %H:%M}–{end:%H:%M}"
    else:
        span = f"{start:%Y-%m-%d %H:%M}–{end:%Y-%m-%d %H:%M}"
    return f"{span} {zone}".strip()


def _pack_messages(
    messages: list[dict], max_chars: int, tz: tzinfo
) -> list[tuple[list[dict], bool]]:
    if max_chars <= 0:
        return [(messages, False)]

    parts: list[tuple[list[dict], bool]] = []
    current: list[dict] = []
    current_len = 0

    for message in messages:
        line_len = len(_message_line(message, tz, context=not message.get("inWindow", True)))
        if line_len > max_chars:
            if current:
                parts.append((current, False))
                current = []
                current_len = 0
            parts.append(([message], True))
            continue

        extra = line_len + (1 if current else 0)
        if current and current_len + extra > max_chars:
            parts.append((current, False))
            current = [message]
            current_len = line_len
        else:
            current.append(message)
            current_len += extra

    if current:
        parts.append((current, False))
    return parts or [(messages, False)]


def build_chunks(
    threads: list[ChatThread],
    window: Window,
    context_before_ms: int,
    max_chars: int,
    tz: tzinfo,
) -> list[ContextChunk]:
    chunks: list[ContextChunk] = []
    for thread in threads:
        packed = _pack_messages(thread.messages, max_chars, tz)
        total = len(packed)
        for index, (messages, oversized) in enumerate(packed, 1):
            first_ts = int(effective_ts(messages[0]) or 0)
            last_ts = int(effective_ts(messages[-1]) or first_ts)
            chunks.append(
                ContextChunk(
                    chunk_id=f"{thread.meta.key}:{first_ts}:{last_ts}:{index}",
                    meta=thread.meta,
                    window=window,
                    context_before_ms=context_before_ms,
                    part=index,
                    parts=total,
                    oversized=oversized,
                    messages=messages,
                )
            )
    return chunks


def render_chunks_ndjson(chunks: list[ContextChunk]) -> list[str]:
    return [json.dumps(chunk.as_dict(), ensure_ascii=False) for chunk in chunks]


def render_chunks_text(chunks: list[ContextChunk], tz: tzinfo) -> list[str]:
    lines: list[str] = []
    for chunk in chunks:
        if lines:
            lines.append("")

        identity = chunk.meta.hash or chunk.meta.id or chunk.meta.name
        kind = "group" if chunk.meta.is_group else "dm"
        part = f" | part {chunk.part}/{chunk.parts}" if chunk.parts > 1 else ""
        lines.append(
            "=== CHAT "
            f"{_safe_header(identity)} | {_safe_header(chunk.meta.name)} | {kind} | "
            f"{_safe_header(chunk.meta.phone or '-')}{part} ==="
        )
        lines.append(f"[window {_window_label(chunk.window, tz)}]")

        has_context = any(not message.get("inWindow", True) for message in chunk.messages)
        if chunk.context_before_ms > 0 and has_context:
            lines.append(f"[context before {_duration_label(chunk.context_before_ms)}]")

        for message in chunk.messages:
            lines.append(_message_line(message, tz, context=not message.get("inWindow", True)))

        if chunk.part < chunk.parts:
            lines.append(
                f"=== END PART {chunk.part}/{chunk.parts} "
                f"(continues in part {chunk.part + 1}/{chunk.parts}) ==="
            )
        else:
            suffix = ", oversized message" if chunk.oversized else ""
            lines.append(f"=== END CHAT ({chunk.part}/{chunk.parts}{suffix}) ===")
    return lines
