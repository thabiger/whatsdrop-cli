"""Full-resolution media export from the encrypted archive."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from conftest import A_JID, ACCOUNT, KEY, TOKEN, make_cfg, seed_archive, ts_at

from whatsdrop_cli.cli import main
from whatsdrop_cli.crypto import (
    derive_keys,
    derive_media_key,
    encrypt_media,
    encrypt_text,
    media_hash,
)
from whatsdrop_cli.fetch import _prefer
from whatsdrop_cli.media import export_media, write_manifest


def _configure(monkeypatch, api):
    monkeypatch.setenv("WHATSDROP_DROPBOX_TOKEN", TOKEN)
    monkeypatch.setenv("WHATSDROP_KEY", KEY)
    monkeypatch.setenv("WHATSDROP_ACCOUNT", ACCOUNT)
    monkeypatch.setenv("WHATSDROP_DROPBOX_BASE", api.endpoint)


def _seed_media(
    api,
    *,
    msg_id="IMG-1",
    payload=b"\xff\xd8\xff\xe0full-resolution",
    path_override=None,
    sha_override=None,
    size_override=None,
    media_status="stored",
):
    hashes = seed_archive(api)
    hash_key, _ = derive_keys(KEY)
    media_key = derive_media_key(KEY)
    chat_folder = hashes["a"]
    relative = (
        path_override
        or f"chat={chat_folder}/dt=2026-10-17/media-{media_hash(hash_key, msg_id)}.enc"
    )
    ts = ts_at(17, 15)
    record = {
        "v": 1,
        "msgId": msg_id,
        "chatId": A_JID,
        "chatName": "Anna Schmidt",
        "isGroup": False,
        "direction": "in",
        "senderName": "Anna",
        "text": "photo",
        "ts": ts,
        "capturedAt": ts + 100,
        "type": "image",
        "source": "store",
        "sortTs": ts,
        "media": {
            "status": media_status,
            "kind": "image",
            "mime": "image/jpeg",
            "size": size_override if size_override is not None else len(payload),
            "width": 1600,
            "height": 1200,
            "sha256": sha_override
            if sha_override is not None
            else hashlib.sha256(payload).hexdigest(),
            "path": relative,
            "capturedAt": ts + 100,
        },
    }
    body = json.dumps(record) + "\n"
    part = f"/account={ACCOUNT}/chat={chat_folder}/dt=2026-10-17/part-{ts}-{ts}-0.enc"
    api.files[part] = encrypt_text(derive_keys(KEY)[1], body)
    api.files[f"/account={ACCOUNT}/{relative}"] = encrypt_media(media_key, relative, payload)
    return record, relative


def test_query_exports_media(api, monkeypatch, tmp_path, capsys):
    _configure(monkeypatch, api)
    _seed_media(api)
    out = tmp_path / "media"
    code = main(
        [
            "query",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-17",
            "--to",
            "2026-10-17",
            "--tz",
            "UTC",
            "--media-out",
            str(out),
        ]
    )
    assert code == 0
    manifest = out / "manifest.ndjson"
    assert manifest.exists()
    entries = [json.loads(line) for line in manifest.read_text().splitlines()]
    exported = [entry for entry in entries if entry["status"] == "exported"]
    assert len(exported) == 1
    path = out / exported[0]["file"].split("/")[-1]
    assert path.exists()
    assert path.read_bytes() == b"\xff\xd8\xff\xe0full-resolution"
    assert path.suffix == ".jpg"
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600


def test_media_path_hash_mismatch(api, monkeypatch, tmp_path):
    _configure(monkeypatch, api)
    _seed_media(
        api,
        path_override="chat=0000000000000000/dt=2026-10-17/media-00112233445566778899aabbccddeeff.enc",
    )
    base = api.endpoint
    cfg = make_cfg(api_base=f"{base}/2", content_base=f"{base}/2", token_url=f"{base}/oauth2/token")
    keys = derive_keys(KEY)
    entry = {
        "msgId": "IMG-1",
        "ts": ts_at(17, 15),
        "chatId": A_JID,
        "type": "image",
        "media": {
            "status": "stored",
            "path": "chat=0000000000000000/dt=2026-10-17/media-00112233445566778899aabbccddeeff.enc",
            "size": 1,
        },
    }
    from whatsdrop_cli.dropbox import DropboxClient

    entries = export_media(DropboxClient(cfg), cfg, keys, [entry], tmp_path)
    assert entries[0]["status"] == "failed"
    assert "mismatch" in entries[0]["error"]


def test_media_sha_mismatch(api, monkeypatch, tmp_path):
    _configure(monkeypatch, api)
    _seed_media(api, sha_override="0" * 64)
    out = tmp_path / "media"
    code = main(
        [
            "query",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-17",
            "--to",
            "2026-10-17",
            "--tz",
            "UTC",
            "--media-out",
            str(out),
        ]
    )
    assert code == 0
    entries = [json.loads(line) for line in (out / "manifest.ndjson").read_text().splitlines()]
    assert any(entry["status"] == "failed" and "sha256" in entry["error"] for entry in entries)


def test_media_missing_object(api, monkeypatch, tmp_path):
    _configure(monkeypatch, api)
    record, relative = _seed_media(api)
    del api.files[f"/account={ACCOUNT}/{relative}"]
    base = api.endpoint
    cfg = make_cfg(api_base=f"{base}/2", content_base=f"{base}/2", token_url=f"{base}/oauth2/token")
    keys = derive_keys(KEY)
    from whatsdrop_cli.dropbox import DropboxClient

    entries = export_media(DropboxClient(cfg), cfg, keys, [record], tmp_path)
    assert entries[0]["status"] == "failed"
    assert "not found" in entries[0]["error"]


def test_media_size_guard(api, monkeypatch, tmp_path):
    _configure(monkeypatch, api)
    _seed_media(api, size_override=10 * 1024 * 1024)
    out = tmp_path / "media"
    code = main(
        [
            "query",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-17",
            "--to",
            "2026-10-17",
            "--tz",
            "UTC",
            "--media-out",
            str(out),
            "--media-max-bytes",
            "10",
        ]
    )
    assert code == 0
    entries = [json.loads(line) for line in (out / "manifest.ndjson").read_text().splitlines()]
    assert any(entry["status"] == "skipped" and "size" in entry["error"] for entry in entries)


def test_media_stored_copy_wins_dedupe():
    pending = {"source": "store", "capturedAt": 2, "media": {"status": "pending"}}
    stored = {"source": "store", "capturedAt": 1, "media": {"status": "stored"}}
    assert _prefer(stored, pending)
    assert not _prefer(pending, stored)


def test_not_downloaded_media_is_skipped(api, monkeypatch, tmp_path):
    _configure(monkeypatch, api)
    record, _relative = _seed_media(api)
    record["media"] = {"status": "not_downloaded", "kind": "image", "capturedAt": ts_at(17, 15)}
    base = api.endpoint
    cfg = make_cfg(api_base=f"{base}/2", content_base=f"{base}/2", token_url=f"{base}/oauth2/token")
    keys = derive_keys(KEY)
    from whatsdrop_cli.dropbox import DropboxClient

    entries = export_media(DropboxClient(cfg), cfg, keys, [record], tmp_path)
    assert entries[0]["status"] == "skipped"
    assert "not_downloaded" in entries[0]["error"]
    assert not list(tmp_path.glob("*.jpg"))


def test_context_exports_media_with_absolute_path(api, monkeypatch, tmp_path, capsys):
    _configure(monkeypatch, api)
    _seed_media(api)
    out = tmp_path / "media"
    code = main(
        [
            "context",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-17",
            "--to",
            "2026-10-17",
            "--tz",
            "UTC",
            "--quiet",
            "--media-out",
            str(out),
        ]
    )

    assert code == 0
    chunks = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    image = next(
        message for chunk in chunks for message in chunk["messages"] if message["msgId"] == "IMG-1"
    )
    exported = image["mediaExport"]
    path = Path(exported["file"])
    assert exported["status"] == "exported"
    assert path.is_absolute()
    assert path.is_file()
    assert path.read_bytes() == b"\xff\xd8\xff\xe0full-resolution"

    manifest = out / "manifest.ndjson"
    entries = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert any(entry["msgId"] == "IMG-1" and entry["status"] == "exported" for entry in entries)


def test_context_exports_media_path_in_text(api, monkeypatch, tmp_path, capsys):
    _configure(monkeypatch, api)
    _seed_media(api)
    out = tmp_path / "media"
    code = main(
        [
            "context",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-17",
            "--to",
            "2026-10-17",
            "--tz",
            "UTC",
            "--quiet",
            "--media-out",
            str(out),
            "--format",
            "text",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "[image] photo" in output
    assert '(file: "' in output
    assert '.jpg")' in output


def test_context_without_media_out_has_no_export(api, monkeypatch, capsys):
    _configure(monkeypatch, api)
    _seed_media(api)
    code = main(
        [
            "context",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-17",
            "--to",
            "2026-10-17",
            "--tz",
            "UTC",
            "--quiet",
        ]
    )

    assert code == 0
    raw = capsys.readouterr().out
    assert '"mediaExport"' not in raw
    chunks = [json.loads(line) for line in raw.splitlines()]
    image = next(
        message for chunk in chunks for message in chunk["messages"] if message["msgId"] == "IMG-1"
    )
    assert "mediaExport" not in image


def test_context_not_downloaded_media_annotated(api, monkeypatch, tmp_path, capsys):
    _configure(monkeypatch, api)
    _seed_media(api, media_status="not_downloaded")
    out = tmp_path / "media"
    code = main(
        [
            "context",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-17",
            "--to",
            "2026-10-17",
            "--tz",
            "UTC",
            "--quiet",
            "--media-out",
            str(out),
            "--format",
            "text",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "(media: not_downloaded)" in output
    assert "(file:" not in output
    entries = [json.loads(line) for line in (out / "manifest.ndjson").read_text().splitlines()]
    assert any(
        entry["msgId"] == "IMG-1"
        and entry["status"] == "skipped"
        and "not_downloaded" in (entry["error"] or "")
        for entry in entries
    )


def test_context_media_size_guard(api, monkeypatch, tmp_path, capsys):
    _configure(monkeypatch, api)
    _seed_media(api, size_override=10 * 1024 * 1024)
    out = tmp_path / "media"
    code = main(
        [
            "context",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-17",
            "--to",
            "2026-10-17",
            "--tz",
            "UTC",
            "--quiet",
            "--media-out",
            str(out),
            "--media-max-bytes",
            "10",
            "--format",
            "text",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "(media: not exported)" in output
    assert "(file:" not in output
    entries = [json.loads(line) for line in (out / "manifest.ndjson").read_text().splitlines()]
    assert any(
        entry["msgId"] == "IMG-1"
        and entry["status"] == "skipped"
        and "size" in (entry["error"] or "")
        for entry in entries
    )


def test_context_media_fail_fast(api, monkeypatch, tmp_path):
    _configure(monkeypatch, api)
    _seed_media(api, sha_override="0" * 64)
    out = tmp_path / "media"
    code = main(
        [
            "context",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-17",
            "--to",
            "2026-10-17",
            "--tz",
            "UTC",
            "--quiet",
            "--media-out",
            str(out),
            "--media-fail-fast",
        ]
    )

    assert code == 5


def test_context_media_custom_manifest(api, monkeypatch, tmp_path):
    _configure(monkeypatch, api)
    _seed_media(api)
    out = tmp_path / "media"
    custom = tmp_path / "custom.ndjson"
    code = main(
        [
            "context",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-17",
            "--to",
            "2026-10-17",
            "--tz",
            "UTC",
            "--quiet",
            "--media-out",
            str(out),
            "--media-manifest",
            str(custom),
        ]
    )

    assert code == 0
    assert custom.exists()
    assert not (out / "manifest.ndjson").exists()


def test_context_media_skips_context_only_chats(api, monkeypatch, tmp_path):
    _configure(monkeypatch, api)
    _seed_media(api)
    out = tmp_path / "media"
    code = main(
        [
            "context",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-17 16:00",
            "--to",
            "2026-10-17 17:00",
            "--tz",
            "UTC",
            "--quiet",
            "--context-before",
            "1d",
            "--media-out",
            str(out),
        ]
    )

    assert code == 3
    assert not (out / "manifest.ndjson").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_write_manifest_sets_private_permissions(tmp_path):
    target = tmp_path / "manifest.ndjson"
    write_manifest(target, [{"msgId": "m1"}])
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
