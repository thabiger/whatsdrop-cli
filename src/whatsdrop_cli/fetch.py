"""Range planning, Dropbox downloads, decoding, filtering and dedupe."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from cryptography.exceptions import InvalidTag

from .archive import ArchiveCodec
from .chats import ChatEntry
from .config import DropboxConfig, Window
from .errors import GuardError, NoMatchesError, RemoteError

MAX_WORKERS = 4
PART_RE = re.compile(r"^part-(\d+)(?:-(\d+))?-(\d+)\.(enc|ndjson)$")


@dataclass(frozen=True)
class FetchTask:
    chat_hash: str
    key: str


def effective_ts(record: dict):
    value = record.get("ts")
    if value is None:
        value = record.get("sortTs")
    if value is None:
        value = record.get("capturedAt")
    return value if isinstance(value, (int, float)) else None


def _media_stored(record: dict) -> bool:
    media = record.get("media")
    return isinstance(media, dict) and media.get("status") == "stored"


def _captured_at(record: dict) -> float:
    value = record.get("capturedAt")
    return value if isinstance(value, (int, float)) else 0


def _prefer(candidate: dict, current: dict) -> bool:
    candidate_store = candidate.get("source") == "store"
    current_store = current.get("source") == "store"
    if candidate_store != current_store:
        return candidate_store
    if _media_stored(candidate) != _media_stored(current):
        return _media_stored(candidate)
    return _captured_at(candidate) > _captured_at(current)


def _dt_from_folder(name: str) -> date | None:
    if not name.startswith("dt="):
        return None
    try:
        return date.fromisoformat(name[3:])
    except ValueError:
        return None


def parse_part_name(name: str, suffix: str | None = None):
    """Return (first_ts, last_ts|None, seq) for a part object."""
    name = name or ""
    if suffix is not None and not name.endswith(suffix):
        return None
    match = PART_RE.match(name)
    if not match:
        return None
    first_ts = int(match.group(1))
    last_ts = int(match.group(2)) if match.group(2) else None
    seq = int(match.group(3))
    return first_ts, last_ts, seq


def _chat_could_have_window(chat: ChatEntry, window: Window) -> bool:
    if chat.last_seen is not None and chat.last_seen < window.start_ms:
        return False
    if chat.first_seen is not None and chat.first_seen >= window.end_ms:
        return False
    return True


def _select_parts(parts: list[dict], window: Window) -> list[dict]:
    """Choose parts that can contain records in [start, end).

    New names carry lastTs (exact interval). Legacy names only carry firstTs:
    take the last legacy part starting at/before the window, plus every later
    part. Parts are written in chronological order, so everything before that
    anchor ends before the window starts (modulo repaired re-uploads).
    """
    ordered = sorted(parts, key=lambda part: (part["first_ts"], part["seq"]))
    selected = [
        part
        for part in ordered
        if part["last_ts"] is not None
        and part["last_ts"] >= window.start_ms
        and part["first_ts"] < window.end_ms
    ]

    anchor = -1
    for index, part in enumerate(ordered):
        if part["last_ts"] is None and part["first_ts"] <= window.start_ms:
            anchor = index
    for index, part in enumerate(ordered):
        if part["last_ts"] is not None:
            continue
        if part["first_ts"] >= window.end_ms:
            continue
        if anchor < 0 or index >= anchor:
            selected.append(part)

    seen = set()
    unique = []
    for part in sorted(selected, key=lambda item: (item["first_ts"], item["seq"])):
        if part["key"] not in seen:
            seen.add(part["key"])
            unique.append(part)
    return unique


def plan_tasks(
    client, cfg: DropboxConfig, codec: ArchiveCodec, chats: list[ChatEntry], window: Window
) -> list[FetchTask]:
    low = datetime.fromtimestamp(window.start_ms / 1000, tz=cfg.tz).date() - timedelta(days=1)
    high = datetime.fromtimestamp(window.end_ms / 1000, tz=cfg.tz).date() + timedelta(days=1)

    tasks: list[FetchTask] = []
    for chat in chats:
        if not _chat_could_have_window(chat, window):
            continue
        base = f"{cfg.account_root}/chat={chat.hash}"
        parts: list[dict] = []
        for item in client.list_folder(base):
            if item.get(".tag") != "folder":
                continue
            day = _dt_from_folder(item.get("name") or "")
            if day is None or not (low <= day <= high):
                continue
            folder_path = item.get("path_display") or item.get("path_lower") or ""
            for part in client.list_folder(folder_path):
                if part.get(".tag") != "file":
                    continue
                parsed = parse_part_name(part.get("name") or "", codec.part_suffix)
                if parsed is None:
                    continue
                path = part.get("path_display") or part.get("path_lower") or ""
                if not path:
                    continue
                first_ts, last_ts, seq = parsed
                parts.append({"key": path, "first_ts": first_ts, "last_ts": last_ts, "seq": seq})
        for part in _select_parts(parts, window):
            tasks.append(FetchTask(chat_hash=chat.hash, key=part["key"]))

    if not tasks:
        raise NoMatchesError("no archived messages found for the matched chats in this window")
    if len(tasks) > cfg.max_parts:
        raise GuardError(
            f"{len(tasks)} part objects exceed --max-parts {cfg.max_parts}; narrow the window or selectors"
        )
    return tasks


def _parse_part(text: str, key: str) -> list[dict]:
    records: list[dict] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RemoteError(f"invalid NDJSON in {key} line {line_number}: {exc}") from exc
        if isinstance(record, dict) and record.get("msgId"):
            records.append(record)
    return records


def collect(
    client, cfg: DropboxConfig, codec: ArchiveCodec, tasks: list[FetchTask], window: Window
) -> list[dict]:
    def fetch(task: FetchTask):
        payload = client.download(task.key)
        if payload is None:
            return task.key, None
        try:
            return task.key, codec.decode_text(payload)
        except (ValueError, InvalidTag) as exc:
            raise RemoteError(f"cannot decrypt {task.key}: {exc}") from exc

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        payloads = list(pool.map(fetch, tasks))

    merged: dict[str, dict] = {}
    for key, text in payloads:
        if text is None:
            continue
        for record in _parse_part(text, key):
            ts = effective_ts(record)
            if ts is None or not (window.start_ms <= ts < window.end_ms):
                continue
            msg_id = str(record["msgId"])
            current = merged.get(msg_id)
            if current is None or _prefer(record, current):
                merged[msg_id] = record

    return sorted(
        merged.values(),
        key=lambda record: (effective_ts(record) or 0, str(record.get("msgId") or "")),
    )
