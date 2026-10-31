"""Dropbox client tests against the local fake server."""

from __future__ import annotations

import pytest
from conftest import APP_KEY, REFRESH_TOKEN, make_cfg

from whatsdrop_cli.dropbox import DropboxClient
from whatsdrop_cli.errors import RemoteError


def cfg(api, **overrides):
    base = api.endpoint
    return make_cfg(
        api_base=f"{base}/2",
        content_base=f"{base}/2",
        token_url=f"{base}/oauth2/token",
        **overrides,
    )


def test_list_and_download(api):
    api.files["/a/one.enc"] = b"payload"
    client = DropboxClient(cfg(api))

    entries = client.list_folder("/a")
    assert [entry["name"] for entry in entries] == ["one.enc"]
    assert client.download("/a/one.enc") == b"payload"
    assert client.download("/a/missing.enc") is None
    assert client.list_folder("/missing") == []


def test_pagination(api):
    api.page_size = 2
    for index in range(5):
        api.files[f"/a/file{index}.enc"] = b"x"
    client = DropboxClient(cfg(api))

    entries = client.list_folder("/a")
    assert len(entries) == 5
    assert len(api.list_calls) >= 3


def test_bad_token(api):
    client = DropboxClient(cfg(api, token="wrong-token"))
    with pytest.raises(RemoteError, match="401"):
        client.list_folder("/a")


def test_missing_scope_guidance(api):
    api.override = (
        401,
        {
            "error_summary": "missing_scope/",
            "error": {".tag": "missing_scope", "required_scope": "files.metadata.read"},
        },
    )
    client = DropboxClient(cfg(api))
    with pytest.raises(RemoteError, match="files.metadata.read"):
        client.list_folder("/a")
    api.override = None


def test_refresh_token_flow(api):
    api.files["/a/one.enc"] = b"payload"
    client = DropboxClient(cfg(api, token=None, refresh_token=REFRESH_TOKEN, app_key=APP_KEY))

    entries = client.list_folder("/a")
    assert [entry["name"] for entry in entries] == ["one.enc"]
    assert api.token_calls == 1
    assert api.valid_tokens


def test_download_missing_returns_none(api):
    client = DropboxClient(cfg(api))
    assert client.download("/nope.enc") is None
