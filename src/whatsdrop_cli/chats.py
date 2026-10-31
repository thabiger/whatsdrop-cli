"""Chat directory loading (decrypted) and selector matching."""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag

from .config import DropboxConfig
from .crypto import chat_hash, decrypt_text
from .errors import RemoteError, UsageError

PHONE_SERVERS = {"c.us", "s.whatsapp.net"}
WILDCARD_CHARS = set("*?[")


@dataclass(frozen=True)
class ChatEntry:
    hash: str
    chat_id: str
    chat_name: str
    chat_phone: str | None = None
    chat_pushname: str | None = None
    is_group: bool = False
    first_seen: int | None = None
    last_seen: int | None = None


def _jid_phone(chat_id: str) -> str | None:
    if not isinstance(chat_id, str) or "@" not in chat_id:
        return None
    user, server = chat_id.rsplit("@", 1)
    if server not in PHONE_SERVERS:
        return None
    return user if user.isdigit() else None


def _entry_from_document(document: dict, fallback_hash: str | None = None) -> ChatEntry | None:
    if not isinstance(document, dict):
        return None
    chat_id = document.get("chatId")
    if not isinstance(chat_id, str) or not chat_id:
        return None
    entry_hash = document.get("hash") or fallback_hash
    if not isinstance(entry_hash, str) or not entry_hash:
        entry_hash = ""
    phone = document.get("chatPhone") or _jid_phone(chat_id)
    return ChatEntry(
        hash=entry_hash,
        chat_id=chat_id,
        chat_name=str(document.get("chatName") or "unknown"),
        chat_phone=str(phone) if phone else None,
        chat_pushname=document.get("chatPushname") or None,
        is_group=bool(document.get("isGroup")),
        first_seen=document.get("firstSeen"),
        last_seen=document.get("lastSeen"),
    )


def load_directory(client, cfg: DropboxConfig, keys: tuple[bytes, bytes]) -> list[ChatEntry]:
    _, enc_key = keys
    index_path = f"{cfg.account_root}/_chats/index.enc"
    payload = client.download(index_path)
    if payload is not None:
        try:
            document = json.loads(decrypt_text(enc_key, payload))
        except (ValueError, InvalidTag, json.JSONDecodeError) as exc:
            raise RemoteError(f"chat index could not be decrypted: {exc}") from exc
        if not isinstance(document, dict):
            raise RemoteError("chat index has an invalid format")
        entries = [_entry_from_document(item) for item in document.get("chats", [])]
        return [entry for entry in entries if entry]

    entries: list[ChatEntry] = []
    for item in client.list_folder(f"{cfg.account_root}/_chats"):
        if item.get(".tag") != "file":
            continue
        name = item.get("name") or ""
        if not name.endswith(".enc") or name == "index.enc":
            continue
        data = client.download(item.get("path_display") or item.get("path_lower") or "")
        if data is None:
            continue
        try:
            document = json.loads(decrypt_text(enc_key, data))
        except (ValueError, InvalidTag, json.JSONDecodeError):
            continue
        if not isinstance(document, dict):
            continue
        entry = _entry_from_document(document, fallback_hash=name[: -len(".enc")])
        if entry:
            entries.append(entry)
    return entries


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 5:
        raise UsageError(f"not a phone number: {value!r}")
    return digits


def _phone_digits(value: object) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) >= 5 else None


def _name_matcher(pattern: str):
    normalized = str(pattern or "").strip().casefold()
    if not normalized:
        return lambda value: False
    if WILDCARD_CHARS.intersection(normalized):
        return lambda value: fnmatch.fnmatchcase(value.casefold(), normalized)
    return lambda value: normalized in value.casefold()


def select_chats(
    entries: list[ChatEntry],
    names: list[str],
    numbers: list[str],
    hashes: list[str],
    jids: list[str],
    hash_key: bytes,
) -> list[ChatEntry]:
    wanted_numbers = {_normalize_phone(number) for number in numbers}
    wanted_hashes = {value.strip().lower() for value in hashes if value.strip()}
    wanted_jids = {value.strip() for value in jids if value.strip()}
    matchers = [_name_matcher(name) for name in names if str(name).strip()]

    selected: list[ChatEntry] = []
    seen: set[str] = set()

    def add(entry: ChatEntry) -> None:
        key = entry.hash or entry.chat_id or f"name:{entry.chat_name.casefold()}"
        if key not in seen:
            seen.add(key)
            selected.append(entry)

    for entry in entries:
        if entry.hash.lower() in wanted_hashes or entry.chat_id in wanted_jids:
            add(entry)
            continue
        if wanted_numbers and (
            (_phone_digits(entry.chat_phone) in wanted_numbers)
            or (_jid_phone(entry.chat_id) in wanted_numbers)
        ):
            add(entry)
            continue
        if matchers and any(
            matcher(entry.chat_name) or (entry.chat_pushname and matcher(entry.chat_pushname))
            for matcher in matchers
        ):
            add(entry)

    for jid in wanted_jids:
        if not any(entry.chat_id == jid for entry in selected):
            add(
                ChatEntry(
                    hash=chat_hash(hash_key, jid),
                    chat_id=jid,
                    chat_name="unknown",
                    chat_phone=_jid_phone(jid),
                    is_group=jid.endswith("@g.us"),
                )
            )

    return selected
