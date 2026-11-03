"""Archive format detection and encrypted object access."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .crypto import (
    chat_hash,
    decrypt_media,
    decrypt_text,
    derive_keys,
    derive_media_key,
    media_hash,
)
from .errors import RemoteError, UsageError

if TYPE_CHECKING:
    from .config import DropboxConfig

ENCRYPTED = "encrypted"
SUPPORTED_FORMAT = 3
FORMAT_OBJECT = "_format.json"


class ArchiveCodec:
    """Read encrypted archive objects."""

    def __init__(self, archive_key: str = "", version: int = SUPPORTED_FORMAT):
        self.version = version
        if not archive_key:
            raise UsageError(
                "archive is encrypted; encryption key required (--key or WHATSDROP_KEY)"
            )
        try:
            self._hash_key, self._enc_key = derive_keys(archive_key)
            self._media_key = derive_media_key(archive_key)
        except ValueError as exc:
            raise UsageError(str(exc)) from exc

    @property
    def part_suffix(self) -> str:
        return ".enc"

    @property
    def chat_suffix(self) -> str:
        return ".enc"

    @property
    def media_suffix(self) -> str:
        return ".enc"

    def decode_text(self, payload: bytes | bytearray) -> str:
        return decrypt_text(self._enc_key, bytes(payload))

    def decode_media(self, relative_path: str, payload: bytes | bytearray) -> bytes:
        return decrypt_media(self._media_key, relative_path, bytes(payload))

    def chat_hash(self, chat_id: str) -> str:
        return chat_hash(self._hash_key, chat_id)

    def media_hash(self, msg_id: str) -> str:
        return media_hash(self._hash_key, msg_id)


def parse_format_marker(payload: bytes) -> tuple[int, str]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteError(f"archive format marker is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise RemoteError("archive format marker is invalid: expected a JSON object")
    layout = document.get("layout")
    if layout is not None and layout != "wa-archive":
        raise RemoteError(f"unsupported archive layout: {layout!r}")

    version = document.get("format")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise RemoteError("archive format marker is invalid: missing format version")
    if version > SUPPORTED_FORMAT:
        raise RemoteError(f"unsupported archive format {version}; update whatsdrop-cli")

    mode = document.get("mode")
    if mode is None:
        if version <= 2:
            mode = ENCRYPTED
        else:
            raise RemoteError("archive format marker is invalid: missing mode")
    if mode == "plaintext":
        raise RemoteError(
            "plaintext archives are no longer supported; re-export with encryption enabled"
        )
    if mode != ENCRYPTED:
        raise RemoteError(f"unsupported archive mode: {mode!r}")
    return version, mode


def read_format(client, cfg: DropboxConfig) -> tuple[int, str]:
    payload = client.download(f"{cfg.account_root}/{FORMAT_OBJECT}")
    if payload is None:
        return 2, ENCRYPTED
    return parse_format_marker(payload)


def resolve_archive(client, cfg: DropboxConfig) -> ArchiveCodec:
    version, _mode = read_format(client, cfg)
    return ArchiveCodec(archive_key=cfg.archive_key, version=version)
