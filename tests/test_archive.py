"""Archive format detection and encrypted codec behaviour."""

from __future__ import annotations

import json

import pytest
from conftest import ACCOUNT, KEY, make_cfg, seed_archive

from whatsdrop_cli.archive import (
    ENCRYPTED,
    ArchiveCodec,
    parse_format_marker,
    resolve_archive,
)
from whatsdrop_cli.dropbox import DropboxClient
from whatsdrop_cli.errors import RemoteError, UsageError


def ctx(api, key=KEY):
    base = api.endpoint
    cfg = make_cfg(
        archive_key=key,
        api_base=f"{base}/2",
        content_base=f"{base}/2",
        token_url=f"{base}/oauth2/token",
    )
    return cfg, DropboxClient(cfg)


def test_plaintext_archive_is_rejected(api):
    seed_archive(api)
    api.files[f"/account={ACCOUNT}/_format.json"] = json.dumps(
        {"format": 3, "layout": "wa-archive", "mode": "plaintext"}
    ).encode()
    cfg, client = ctx(api, key="")

    with pytest.raises(RemoteError, match="plaintext archives are no longer supported"):
        resolve_archive(client, cfg)


def test_encrypted_archive_requires_key(api):
    seed_archive(api)
    cfg, client = ctx(api, key="")

    with pytest.raises(UsageError, match="encryption key required"):
        resolve_archive(client, cfg)


def test_encrypted_archive_with_key(api):
    seed_archive(api)
    cfg, client = ctx(api)

    codec = resolve_archive(client, cfg)

    assert codec.version == 3
    assert codec.part_suffix == ".enc"


def test_missing_format_marker_is_legacy_encrypted(api):
    seed_archive(api, format_marker=False)
    cfg, client = ctx(api)

    codec = resolve_archive(client, cfg)

    assert codec.version == 2


def test_malformed_format_marker(api):
    seed_archive(api)
    api.files[f"/account={ACCOUNT}/_format.json"] = b"not json"
    cfg, client = ctx(api)

    with pytest.raises(RemoteError, match="format marker is invalid"):
        resolve_archive(client, cfg)


def test_unsupported_format_version(api):
    seed_archive(api)
    marker = {"format": 4, "layout": "wa-archive", "mode": "encrypted"}
    api.files[f"/account={ACCOUNT}/_format.json"] = json.dumps(marker).encode()
    cfg, client = ctx(api)

    with pytest.raises(RemoteError, match="unsupported archive format"):
        resolve_archive(client, cfg)


def test_unsupported_format_mode(api):
    seed_archive(api)
    marker = {"format": 3, "layout": "wa-archive", "mode": "rot13"}
    api.files[f"/account={ACCOUNT}/_format.json"] = json.dumps(marker).encode()
    cfg, client = ctx(api)

    with pytest.raises(RemoteError, match="unsupported archive mode"):
        resolve_archive(client, cfg)


def test_parse_format_marker_accepts_v2_without_mode():
    version, mode = parse_format_marker(b'{"format": 2, "layout": "wa-archive"}')

    assert version == 2
    assert mode == ENCRYPTED


def test_invalid_encryption_key_is_a_usage_error():
    with pytest.raises(UsageError, match="must be 32 bytes"):
        ArchiveCodec(archive_key="YWJj")
