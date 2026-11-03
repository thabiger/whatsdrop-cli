"""Download, decrypt, and export full-resolution media."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cryptography.exceptions import InvalidTag

from .archive import ArchiveCodec
from .config import DropboxConfig
from .errors import RemoteError

MEDIA_PATH_RE = re.compile(
    r"^chat=([0-9a-f]{16})/dt=(\d{4}-\d{2}-\d{2})/media-([0-9a-f]{32})\.(enc|bin)$"
)
MAX_WORKERS = 4

MIME_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _extension(data: bytes, mime: str | None) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return MIME_EXTENSIONS.get(str(mime or "").lower(), "bin")


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "message"))
    return cleaned.strip("._")[:64] or "message"


def _timestamp(record: dict) -> int:
    for key in ("ts", "sortTs", "capturedAt"):
        value = record.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _validate_path(record: dict, codec: ArchiveCodec) -> tuple[str, str]:
    media = record.get("media") if isinstance(record.get("media"), dict) else {}
    path = str(media.get("path") or "").strip()
    match = MEDIA_PATH_RE.match(path)
    if not match:
        raise ValueError("invalid media path")
    if f".{match.group(4)}" != codec.media_suffix:
        raise ValueError("media path does not match archive mode")

    chat_id = record.get("chatId")
    if not isinstance(chat_id, str) or not chat_id:
        raise ValueError("media record has no chat id")
    if match.group(1) != codec.chat_hash(chat_id):
        raise ValueError("media path chat hash mismatch")
    if match.group(3) != codec.media_hash(str(record.get("msgId") or "")):
        raise ValueError("media path message hash mismatch")
    return path, match.group(2)


def _write_media(out_dir: Path, record: dict, data: bytes, mime: str | None) -> Path:
    ts = _timestamp(record)
    target = out_dir / f"{ts}-{_safe_id(str(record.get('msgId') or ''))}.{_extension(data, mime)}"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(out_dir), prefix=".media-", suffix=".part"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def export_media(
    client,
    cfg: DropboxConfig,
    codec: ArchiveCodec,
    records: list[dict],
    out_dir: str | Path,
    *,
    max_bytes: int = 25 * 1024 * 1024,
    fail_fast: bool = False,
) -> list[dict]:
    directory = Path(out_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    except OSError as exc:
        raise RemoteError(f"cannot create media directory {directory}: {exc}") from exc

    def run(record: dict) -> dict:
        media = record.get("media") if isinstance(record.get("media"), dict) else None
        entry = {
            "msgId": record.get("msgId"),
            "ts": _timestamp(record),
            "chatId": record.get("chatId"),
            "chatName": record.get("chatName"),
            "senderName": record.get("senderName"),
            "direction": record.get("direction"),
            "type": record.get("type"),
            "media": media,
            "status": "skipped",
            "file": None,
            "error": None,
        }
        if not media:
            entry["error"] = "no media"
            return entry
        if media.get("status") != "stored":
            entry["error"] = f"media status {media.get('status') or 'unknown'}"
            return entry
        try:
            relative, _day = _validate_path(record, codec)
            expected_size = media.get("size")
            if isinstance(expected_size, (int, float)) and expected_size > max_bytes:
                entry["error"] = "media exceeds size limit"
                return entry
            payload = client.download(f"{cfg.account_root}/{relative}")
            if payload is None:
                entry["status"] = "failed"
                entry["error"] = "media object not found"
                return entry
            decoded = codec.decode_media(relative, payload)
            if isinstance(expected_size, (int, float)) and len(decoded) != int(expected_size):
                raise ValueError("media size mismatch")
            expected_hash = media.get("sha256")
            if expected_hash and hashlib.sha256(decoded).hexdigest() != str(expected_hash):
                raise ValueError("media sha256 mismatch")
            if len(decoded) > max_bytes:
                entry["error"] = "media exceeds size limit"
                return entry
            path = _write_media(directory, record, decoded, media.get("mime"))
            entry.update({"status": "exported", "file": str(path)})
        except (ValueError, InvalidTag, OSError) as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
            if fail_fast:
                raise RemoteError(f"cannot export media for {record.get('msgId')}: {exc}") from exc
        return entry

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        entries = list(pool.map(run, records))
    return entries


def write_manifest(path: str | Path, entries: list[dict]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    with os.fdopen(os.open(target, flags, 0o600), "w", encoding="utf-8", newline="\n") as handle:
        os.chmod(target, 0o600)
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
