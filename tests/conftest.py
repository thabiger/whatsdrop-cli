"""Shared fake Dropbox server, archive fixtures and config helpers."""

from __future__ import annotations

import base64
import json
import posixpath
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from whatsdrop_cli.config import DropboxConfig
from whatsdrop_cli.crypto import chat_hash, derive_keys, encrypt_text


@pytest.fixture(autouse=True)
def _isolate_config_home(tmp_path_factory, monkeypatch):
    config_home = tmp_path_factory.mktemp("config-home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("APPDATA", str(config_home))


KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
TOKEN = "test-dropbox-token"
REFRESH_TOKEN = "test-refresh-token"
APP_KEY = "test-app-key"
ACCOUNT = "primary"

# Synthetic fixtures for tests and the shared crypto known-answer vector.
A_JID = "49171234567@c.us"
LID_JID = "123456789012345@lid"
GROUP_JID = "120363000000000000@g.us"


def ts_at(day: int, hour: int = 12, minute: int = 0) -> int:
    return int(datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def make_cfg(**overrides) -> DropboxConfig:
    values = dict(
        token=TOKEN,
        refresh_token=None,
        app_key=None,
        app_secret=None,
        archive_key=KEY,
        account=ACCOUNT,
        folder="",
        tz=timezone.utc,
        timeout=10,
        max_parts=1000,
    )
    values.update(overrides)
    return DropboxConfig(**values)


def message(
    msg_id: str,
    *,
    chat_id: str,
    chat_name: str,
    ts_ms: int,
    source: str = "store",
    text: str | None = None,
    sender_name: str = "Anna",
    direction: str = "in",
    captured_at: int | None = None,
    type_: str = "chat",
    chat_phone: str | None = None,
) -> dict:
    record = {
        "v": 1,
        "msgId": msg_id,
        "chatId": chat_id,
        "chatName": chat_name,
        "isGroup": chat_id.endswith("@g.us"),
        "direction": direction,
        "senderName": sender_name,
        "text": text if text is not None else f"text {msg_id}",
        "ts": ts_ms,
        "capturedAt": captured_at or ts_ms + 100,
        "type": type_,
        "source": source,
        "sortTs": ts_ms,
    }
    if chat_phone:
        record["chatPhone"] = chat_phone
    return record


class FakeDropbox:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.valid_tokens = {TOKEN}
        self.refreshed_token = "refreshed-token"
        self.page_size = 1000
        self.list_calls: list[str] = []
        self.download_calls: list[str] = []
        self.token_calls = 0
        self.token_requests: list[dict] = []
        self.token_error: tuple[int, dict] | None = None
        self.issued_refresh_token = "issued-refresh-token"
        self.account_calls = 0
        self.revoked_tokens: list[str] = []
        self.override: tuple[int, dict] | None = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> FakeDropbox:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    # --- tree helpers -------------------------------------------------------
    @staticmethod
    def _parent(path: str) -> str:
        parent = posixpath.dirname(path)
        return "" if parent in ("", "/") else parent

    def _children(self, path: str) -> list[dict]:
        path = (path or "").rstrip("/")
        files: list[dict] = []
        dirs: set[str] = set()
        for file_path in self.files:
            parts = file_path.strip("/").split("/")
            for index in range(1, len(parts)):
                dirs.add("/" + "/".join(parts[:index]))
            if self._parent(file_path) == path:
                files.append(
                    {
                        ".tag": "file",
                        "name": posixpath.basename(file_path),
                        "path_display": file_path,
                    }
                )
        folder_entries = [
            {".tag": "folder", "name": posixpath.basename(directory), "path_display": directory}
            for directory in dirs
            if self._parent(directory) == path
        ]
        return sorted(folder_entries + files, key=lambda item: item["name"])

    def _page(self, path: str, offset: int) -> dict:
        entries = self._children(path)
        chunk = entries[offset : offset + self.page_size]
        next_offset = offset + len(chunk)
        cursor = base64.urlsafe_b64encode(
            json.dumps({"path": path, "offset": next_offset}).encode()
        ).decode()
        return {"entries": chunk, "has_more": next_offset < len(entries), "cursor": cursor}

    # --- HTTP server --------------------------------------------------------
    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                path = self.path

                if path == "/oauth2/token":
                    api.token_calls += 1
                    form = dict(urllib.parse.parse_qsl(raw.decode("utf-8")))
                    api.token_requests.append(form)
                    if api.token_error is not None:
                        status, payload = api.token_error
                        return api._send(
                            self, status, json.dumps(payload).encode(), "application/json"
                        )
                    grant = form.get("grant_type")
                    access = api.refreshed_token
                    api.valid_tokens.add(access)
                    response = {"access_token": access, "expires_in": 14400}
                    if grant == "authorization_code":
                        response["refresh_token"] = api.issued_refresh_token
                        response["scope"] = (
                            "account_info.read files.content.read files.metadata.read"
                        )
                        response["account_id"] = "dbid:test"
                    body = json.dumps(response).encode()
                    return api._send(self, 200, body, "application/json")

                if api.override is not None:
                    status, payload = api.override
                    return api._send(self, status, json.dumps(payload).encode(), "application/json")

                token = (self.headers.get("authorization") or "").replace("Bearer ", "")
                if token not in api.valid_tokens:
                    return api._send(
                        self,
                        401,
                        json.dumps(
                            {
                                "error_summary": "invalid_access_token/",
                                "error": {".tag": "invalid_access_token"},
                            }
                        ).encode(),
                        "application/json",
                    )

                if path == "/2/files/list_folder":
                    body = json.loads(raw or b"{}")
                    folder = body.get("path") or ""
                    api.list_calls.append(folder)
                    return api._send(
                        self, 200, json.dumps(api._page(folder, 0)).encode(), "application/json"
                    )

                if path == "/2/files/list_folder/continue":
                    body = json.loads(raw or b"{}")
                    cursor = json.loads(base64.urlsafe_b64decode(body.get("cursor") or ""))
                    api.list_calls.append(cursor["path"])
                    return api._send(
                        self,
                        200,
                        json.dumps(api._page(cursor["path"], cursor["offset"])).encode(),
                        "application/json",
                    )

                if path == "/2/files/download":
                    argument = json.loads(self.headers.get("dropbox-api-arg") or "{}")
                    key = argument.get("path") or ""
                    api.download_calls.append(key)
                    if key not in api.files:
                        return api._send(
                            self,
                            409,
                            json.dumps(
                                {
                                    "error_summary": "path/not_found/",
                                    "error": {".tag": "path", "path": {".tag": "not_found"}},
                                }
                            ).encode(),
                            "application/json",
                        )
                    return api._send(self, 200, api.files[key], "application/octet-stream")

                if path == "/2/users/get_current_account":
                    api.account_calls += 1
                    body = json.dumps(
                        {
                            "name": {"display_name": "Test User"},
                            "email": "test@example.com",
                            "account_id": "dbid:test",
                        }
                    ).encode()
                    return api._send(self, 200, body, "application/json")

                if path == "/2/auth/token/revoke":
                    token = (self.headers.get("authorization") or "").replace("Bearer ", "")
                    api.revoked_tokens.append(token)
                    api.valid_tokens.discard(token)
                    return api._send(self, 200, b"", "application/json")

                return api._send(self, 404, b"{}", "application/json")

        return Handler

    @staticmethod
    def _send(
        handler: BaseHTTPRequestHandler, status: int, payload: bytes, content_type: str
    ) -> None:
        handler.send_response(status)
        handler.send_header("content-type", content_type)
        handler.send_header("content-length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)


def format_document() -> dict:
    return {
        "format": 3,
        "layout": "wa-archive",
        "mode": "encrypted",
        "encryption": "aes-256-gcm",
        "chatHash": "hmac-sha256-16",
        "keyDerivation": "hkdf-sha256",
        "media": {
            "layout": "wa-media-v1",
            "encryption": "aes-256-gcm",
            "hash": "hmac-sha256-32",
            "keyDerivation": "hkdf-sha256",
        },
    }


def seed_archive(
    api: FakeDropbox,
    key: str = KEY,
    format_marker: bool = True,
) -> dict[str, str]:
    hash_key, enc_key = derive_keys(key)
    hashes = {
        "a": chat_hash(hash_key, A_JID),
        "lid": chat_hash(hash_key, LID_JID),
        "group": chat_hash(hash_key, GROUP_JID),
    }
    chats = [
        {
            "hash": hashes["a"],
            "chatId": A_JID,
            "chatName": "Anna Schmidt",
            "chatPhone": "49171234567",
            "chatPushname": "Anna",
            "isGroup": False,
            "firstSeen": ts_at(16),
            "lastSeen": ts_at(17),
        },
        {
            "hash": hashes["lid"],
            "chatId": LID_JID,
            "chatName": "Bob LID",
            "chatPhone": "49170000000",
            "chatPushname": "Bobby",
            "isGroup": False,
            "firstSeen": ts_at(17),
            "lastSeen": ts_at(17),
        },
        {
            "hash": hashes["group"],
            "chatId": GROUP_JID,
            "chatName": "Family Group",
            "chatPhone": None,
            "chatPushname": None,
            "isGroup": True,
            "firstSeen": ts_at(17),
            "lastSeen": ts_at(17),
        },
    ]
    chat_suffix = ".enc"
    part_suffix = ".enc"

    def encode(text: str) -> bytes:
        return encrypt_text(enc_key, text)

    if format_marker:
        api.files[f"/account={ACCOUNT}/_format.json"] = json.dumps(format_document()).encode(
            "utf-8"
        )

    index_document = {
        "generatedAt": "2026-10-17T12:00:00Z",
        "mode": "encrypted",
        "chats": chats,
    }
    api.files[f"/account={ACCOUNT}/_chats/index{chat_suffix}"] = encode(json.dumps(index_document))
    for chat in chats:
        payload = {k: v for k, v in chat.items() if k != "hash"}
        api.files[f"/account={ACCOUNT}/_chats/{chat['hash']}{chat_suffix}"] = encode(
            json.dumps(payload)
        )

    def put_part(chat_hash_value: str, day: str, name: str, records: list[dict]) -> None:
        body = "\n".join(json.dumps(record) for record in records) + "\n"
        path = f"/account={ACCOUNT}/chat={chat_hash_value}/dt={day}/{name}{part_suffix}"
        api.files[path] = encode(body)

    put_part(
        hashes["a"],
        "2026-10-16",
        "part-1792195140000-0",
        [
            message(
                "A1",
                chat_id=A_JID,
                chat_name="Anna Schmidt",
                ts_ms=ts_at(16, 23, 59),
                text="late message",
            )
        ],
    )
    put_part(
        hashes["a"],
        "2026-10-17",
        "part-1792228800000-1792228800000-0",
        [
            message(
                "A2",
                chat_id=A_JID,
                chat_name="Anna Schmidt",
                ts_ms=ts_at(17),
                text="original",
                captured_at=ts_at(17) + 100,
            )
        ],
    )
    put_part(
        hashes["a"],
        "2026-10-17",
        "part-1792228800000-1792228800000-1",
        [
            message(
                "A2",
                chat_id=A_JID,
                chat_name="Anna Schmidt",
                ts_ms=ts_at(17),
                source="dom",
                text="dom copy",
                captured_at=ts_at(17) + 200,
            )
        ],
    )
    put_part(
        hashes["a"],
        "2026-10-17",
        "part-1792228800000-1792228800000-2",
        [
            message(
                "A2",
                chat_id=A_JID,
                chat_name="Anna Schmidt",
                ts_ms=ts_at(17),
                text="repaired copy",
                captured_at=ts_at(17) + 900,
            )
        ],
    )
    put_part(
        hashes["lid"],
        "2026-10-17",
        "part-1792228800000-1792228800000-0",
        [
            message(
                "L1",
                chat_id=LID_JID,
                chat_name="Bob LID",
                ts_ms=ts_at(17, 13),
                chat_phone="49170000000",
                sender_name="Bob",
                text="lid message",
            )
        ],
    )
    put_part(
        hashes["group"],
        "2026-10-17",
        "part-1792228800000-1792228800000-0",
        [
            message(
                "G1",
                chat_id=GROUP_JID,
                chat_name="Family Group",
                ts_ms=ts_at(17, 14),
                sender_name="Mark",
                text="Hi, family",
            )
        ],
    )
    return hashes


@pytest.fixture()
def api():
    server = FakeDropbox().start()
    yield server
    server.stop()


@pytest.fixture()
def seeded(api):
    seed_archive(api)
    return api
