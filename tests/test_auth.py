"""Tests for Dropbox OAuth (PKCE, token exchange, credentials store, flows)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import stat
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from urllib.parse import parse_qs, urlparse

import pytest

from whatsdrop_cli.auth import (
    CLI_SCOPES,
    CREDENTIALS_VERSION,
    DEFAULT_APP_KEY,
    REQUIRED_SCOPES,
    Credentials,
    build_authorize_url,
    default_credentials_path,
    delete_credentials,
    exchange_code,
    generate_pkce,
    load_credentials,
    missing_scopes,
    parse_token_document,
    refresh_access_token,
    save_credentials,
)
from whatsdrop_cli.auth_flow import run_loopback_flow, run_paste_flow
from whatsdrop_cli.errors import RemoteError, UsageError

# --- PKCE -------------------------------------------------------------------


def test_generate_pkce_verifier_format():
    verifier, challenge = generate_pkce()
    assert len(verifier) >= 43
    assert verifier == verifier.strip()
    assert all(c.isalnum() or c in "-_" for c in verifier)


def test_generate_pkce_challenge_is_sha256():
    verifier, challenge = generate_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected


def test_generate_pkce_unique():
    v1, c1 = generate_pkce()
    v2, c2 = generate_pkce()
    assert v1 != v2
    assert c1 != c2


# --- build_authorize_url ----------------------------------------------------


def test_build_authorize_url_basic():
    url = build_authorize_url("my-app-key", code_challenge="abc123")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.dropbox.com"
    assert parsed.path == "/oauth2/authorize"
    params = parse_qs(parsed.query)
    assert params["client_id"] == ["my-app-key"]
    assert params["response_type"] == ["code"]
    assert params["token_access_type"] == ["offline"]
    assert params["code_challenge"] == ["abc123"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == [" ".join(CLI_SCOPES)]


def test_build_authorize_url_with_redirect_and_state():
    url = build_authorize_url(
        "key", code_challenge="ch", redirect_uri="http://127.0.0.1:53682/callback", state="xyz"
    )
    params = parse_qs(urlparse(url).query)
    assert params["redirect_uri"] == ["http://127.0.0.1:53682/callback"]
    assert params["state"] == ["xyz"]


def test_build_authorize_url_custom_scopes():
    url = build_authorize_url("key", code_challenge="ch", scopes=("files.metadata.read",))
    params = parse_qs(urlparse(url).query)
    assert params["scope"] == ["files.metadata.read"]


# --- token exchange / refresh (against fake server) -------------------------


def test_exchange_code_success(api):
    document = exchange_code(
        app_key="test-app-key",
        code="auth-code-123",
        code_verifier="verifier-456",
        token_url=f"{api.endpoint}/oauth2/token",
        timeout=10,
    )
    assert document["access_token"] == api.refreshed_token
    assert document["refresh_token"] == api.issued_refresh_token
    assert document["account_id"] == "dbid:test"
    assert api.token_calls == 1
    form = api.token_requests[0]
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "auth-code-123"
    assert form["client_id"] == "test-app-key"
    assert form["code_verifier"] == "verifier-456"


def test_exchange_code_with_redirect_uri(api):
    exchange_code(
        app_key="key",
        code="c",
        code_verifier="v",
        token_url=f"{api.endpoint}/oauth2/token",
        redirect_uri="http://127.0.0.1:53682/callback",
        timeout=10,
    )
    assert api.token_requests[0]["redirect_uri"] == "http://127.0.0.1:53682/callback"


def test_exchange_code_with_client_secret(api):
    exchange_code(
        app_key="key",
        code="c",
        code_verifier="v",
        token_url=f"{api.endpoint}/oauth2/token",
        client_secret="s3cret",
        timeout=10,
    )
    assert api.token_requests[0]["client_secret"] == "s3cret"


def test_exchange_code_invalid_grant(api):
    api.token_error = (400, {"error": "invalid_grant", "error_description": "code expired"})
    with pytest.raises(RemoteError, match="authorization code expired"):
        exchange_code(
            app_key="key",
            code="c",
            code_verifier="v",
            token_url=f"{api.endpoint}/oauth2/token",
            timeout=10,
        )


def test_refresh_access_token_success(api):
    document = refresh_access_token(
        app_key="test-app-key",
        refresh_token="rt-123",
        token_url=f"{api.endpoint}/oauth2/token",
        timeout=10,
    )
    assert document["access_token"] == api.refreshed_token
    form = api.token_requests[0]
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "rt-123"


def test_refresh_access_token_invalid_grant(api):
    api.token_error = (400, {"error": "invalid_grant", "error_description": "revoked"})
    with pytest.raises(RemoteError, match="refresh token was revoked"):
        refresh_access_token(
            app_key="key",
            refresh_token="rt",
            token_url=f"{api.endpoint}/oauth2/token",
            timeout=10,
        )


def test_exchange_code_unauthorized_client(api):
    api.token_error = (401, {"error": "unauthorized_client"})
    with pytest.raises(RemoteError, match="rejected the app key"):
        exchange_code(
            app_key="bad",
            code="c",
            code_verifier="v",
            token_url=f"{api.endpoint}/oauth2/token",
            timeout=10,
        )


# --- parse_token_document ---------------------------------------------------


def test_parse_token_document_full():
    doc = {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_in": 14400,
        "scope": "files.metadata.read files.content.read",
        "account_id": "dbid:123",
    }
    parsed = parse_token_document(doc)
    assert parsed["access_token"] == "at"
    assert parsed["refresh_token"] == "rt"
    assert parsed["expires_at"] is not None
    assert parsed["expires_at"] > time.time()
    assert parsed["scope"] == ["files.metadata.read", "files.content.read"]
    assert parsed["account_id"] == "dbid:123"


def test_parse_token_document_no_expiry():
    doc = {"access_token": "at"}
    parsed = parse_token_document(doc)
    assert parsed["expires_at"] is None
    assert parsed["refresh_token"] is None
    assert parsed["scope"] == []


def test_parse_token_document_missing_access_token():
    with pytest.raises(RemoteError, match="no access token"):
        parse_token_document({"refresh_token": "rt"})


# --- missing_scopes ---------------------------------------------------------


def test_missing_scopes_none():
    assert missing_scopes(["files.metadata.read", "files.content.read"]) == []


def test_missing_scopes_partial():
    result = missing_scopes(["files.metadata.read"])
    assert result == ["files.content.read"]


def test_missing_scopes_empty():
    assert len(missing_scopes([])) == len(REQUIRED_SCOPES)


def test_missing_scopes_none_input():
    assert len(missing_scopes(None)) == len(REQUIRED_SCOPES)


# --- Credentials serialization ----------------------------------------------


def test_credentials_roundtrip():
    creds = Credentials(
        app_key="key",
        refresh_token="rt",
        access_token="at",
        expires_at=12345.0,
        scope=["files.metadata.read"],
        account_id="dbid:1",
        account_name="Test User",
        email="test@example.com",
        obtained_at="2026-10-17T12:00:00+00:00",
    )
    data = creds.to_dict()
    assert data["version"] == CREDENTIALS_VERSION
    restored = Credentials.from_dict(data)
    assert restored.app_key == creds.app_key
    assert restored.refresh_token == creds.refresh_token
    assert restored.access_token == creds.access_token
    assert restored.expires_at == creds.expires_at
    assert restored.scope == creds.scope
    assert restored.account_id == creds.account_id
    assert restored.account_name == creds.account_name
    assert restored.email == creds.email
    assert restored.obtained_at == creds.obtained_at


def test_credentials_from_dict_minimal():
    creds = Credentials.from_dict({"app_key": "key", "refresh_token": "rt"})
    assert creds.app_key == "key"
    assert creds.refresh_token == "rt"
    assert creds.access_token is None
    assert creds.expires_at is None
    assert creds.scope == []


# --- credentials file I/O ---------------------------------------------------


def test_save_and_load_credentials(tmp_path):
    path = tmp_path / "creds.json"
    creds = Credentials(app_key="key", refresh_token="rt", access_token="at")
    save_credentials(path, creds)
    loaded = load_credentials(path)
    assert loaded is not None
    assert loaded.app_key == "key"
    assert loaded.refresh_token == "rt"
    assert loaded.access_token == "at"


def test_save_credentials_permissions(tmp_path):
    path = tmp_path / "creds.json"
    save_credentials(path, Credentials(app_key="k", refresh_token="r"))
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_save_credentials_creates_parent(tmp_path):
    path = tmp_path / "nested" / "dir" / "creds.json"
    save_credentials(path, Credentials(app_key="k", refresh_token="r"))
    assert path.exists()


def test_save_credentials_creates_private_parent(tmp_path):
    path = tmp_path / "private" / "creds.json"
    save_credentials(path, Credentials(app_key="k", refresh_token="r"))
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_load_credentials_missing_file(tmp_path):
    assert load_credentials(tmp_path / "nonexistent.json") is None


def test_load_credentials_bad_json(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text("not json")
    assert load_credentials(path) is None


def test_load_credentials_wrong_version(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text(json.dumps({"version": 999, "app_key": "k"}))
    assert load_credentials(path) is None


def test_load_credentials_missing_app_key(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text(json.dumps({"version": CREDENTIALS_VERSION}))
    assert load_credentials(path) is None


def test_delete_credentials(tmp_path):
    path = tmp_path / "creds.json"
    save_credentials(path, Credentials(app_key="k", refresh_token="r"))
    assert delete_credentials(path) is True
    assert not path.exists()
    assert delete_credentials(path) is False


def test_save_credentials_no_temp_files_left(tmp_path):
    path = tmp_path / "creds.json"
    save_credentials(path, Credentials(app_key="k", refresh_token="r"))
    leftovers = [f for f in tmp_path.iterdir() if f.name.startswith(".credentials-")]
    assert leftovers == []


def test_save_credentials_overwrite(tmp_path):
    path = tmp_path / "creds.json"
    save_credentials(path, Credentials(app_key="first", refresh_token="rt1"))
    save_credentials(path, Credentials(app_key="second", refresh_token="rt2"))
    loaded = load_credentials(path)
    assert loaded.app_key == "second"
    assert loaded.refresh_token == "rt2"


# --- default_credentials_path -----------------------------------------------


def test_default_credentials_path_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    path = default_credentials_path()
    assert path.name == "credentials.json"
    assert "whatsdrop" in str(path)


def test_default_credentials_path_xdg(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/config")
    path = default_credentials_path()
    assert str(path).startswith("/custom/config/whatsdrop/")


# --- run_paste_flow ---------------------------------------------------------


def test_run_paste_flow_with_code(api):
    output: list[str] = []
    parsed = run_paste_flow(
        app_key="test-app-key",
        token_url=f"{api.endpoint}/oauth2/token",
        code="pre-supplied-code",
        open_browser=False,
        print_fn=output.append,
        timeout=10,
    )
    assert parsed["access_token"] == api.refreshed_token
    assert parsed["refresh_token"] == api.issued_refresh_token
    assert any("Open this URL" in line for line in output)


def test_run_paste_flow_empty_code():
    with pytest.raises(UsageError, match="no authorization code"):
        run_paste_flow(
            app_key="key",
            token_url="http://127.0.0.1:1/oauth2/token",
            code="",
            open_browser=False,
            print_fn=lambda _: None,
            timeout=1,
        )


def test_run_paste_flow_non_tty_without_code(monkeypatch):
    import sys

    class FakeStdin:
        def isatty(self):
            return False

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(UsageError, match="non-interactive"):
        run_paste_flow(
            app_key="key",
            token_url="http://127.0.0.1:1/oauth2/token",
            code=None,
            open_browser=False,
            print_fn=lambda _: None,
            timeout=1,
        )


def test_run_paste_flow_with_input(api, monkeypatch):
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    parsed = run_paste_flow(
        app_key="test-app-key",
        token_url=f"{api.endpoint}/oauth2/token",
        code=None,
        input_fn=lambda prompt: "pasted-code-789",
        open_browser=False,
        print_fn=lambda _: None,
        timeout=10,
    )
    assert parsed["access_token"] == api.refreshed_token
    assert api.token_requests[0]["code"] == "pasted-code-789"


# --- constants --------------------------------------------------------------


def test_cli_scopes_read_only():
    assert "files.content.write" not in CLI_SCOPES
    assert "files.metadata.read" in CLI_SCOPES
    assert "files.content.read" in CLI_SCOPES
    assert "account_info.read" in CLI_SCOPES


def test_required_scopes_subset_of_cli():
    for scope in REQUIRED_SCOPES:
        assert scope in CLI_SCOPES


def test_default_app_key_is_string():
    assert isinstance(DEFAULT_APP_KEY, str)


def test_default_app_key_matches_release():
    assert DEFAULT_APP_KEY == "ydtsl7okrf4xuee"


def test_loopback_rejects_bad_state_and_accepts_valid_state(api, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    captured: dict[str, str] = {}
    monkeypatch.setattr(webbrowser, "open", lambda url: captured.setdefault("url", url))

    result: dict[str, object] = {}

    def run() -> None:
        try:
            result["value"] = run_loopback_flow(
                app_key="test-app-key",
                token_url=f"{api.endpoint}/oauth2/token",
                port=port,
                open_browser=True,
                timeout=5,
                print_fn=lambda _: None,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            result["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.time() + 3
    while "url" not in captured and time.time() < deadline:
        time.sleep(0.01)
    assert "url" in captured

    state = parse_qs(urlparse(captured["url"]).query)["state"][0]
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?state=wrong&code=bad", timeout=2)
    assert error.value.code == 400
    error.value.close()

    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/callback?state={state}&code=good", timeout=2
    ) as response:
        assert response.status == 200

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "error" not in result
    assert result["value"]["access_token"] == api.refreshed_token
