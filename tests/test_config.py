"""Unit tests for config/bounds/durations/windows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from whatsdrop_cli.auth import Credentials, save_credentials
from whatsdrop_cli.config import (
    DEFAULT_APP_KEY,
    load_config,
    parse_bound,
    parse_context_before,
    parse_duration,
    parse_tz,
    resolve_auth,
    resolve_window,
    snap_end_exclusive,
    snap_start,
)
from whatsdrop_cli.errors import UsageError

UTC = timezone.utc
NOW = datetime(2026, 10, 17, 15, 30, 45, tzinfo=UTC)


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def test_parse_bound_precisions():
    moment, precision = parse_bound("2026-10-01", UTC)
    assert (moment, precision) == (datetime(2026, 10, 1, tzinfo=UTC), "date")

    moment, precision = parse_bound("2026-10-01T14", UTC)
    assert (moment, precision) == (datetime(2026, 10, 1, 14, tzinfo=UTC), "hour")

    moment, precision = parse_bound("2026-10-01 14:30", UTC)
    assert (moment, precision) == (datetime(2026, 10, 1, 14, 30, tzinfo=UTC), "minute")

    moment, precision = parse_bound("2026-10-01T14:30:45", UTC)
    assert (moment, precision) == (datetime(2026, 10, 1, 14, 30, 45, tzinfo=UTC), "second")


@pytest.mark.parametrize(
    "text", ["", "2026-13-01", "2026-10-32", "01.10.2026", "2026-10-01T25", "hello"]
)
def test_parse_bound_rejects_invalid(text):
    with pytest.raises(UsageError):
        parse_bound(text, UTC)


def test_snap_behaviour():
    moment = datetime(2026, 10, 1, 14, 30, 45, tzinfo=UTC)
    assert snap_start(moment, "date") == datetime(2026, 10, 1, tzinfo=UTC)
    assert snap_start(moment, "hour") == datetime(2026, 10, 1, 14, tzinfo=UTC)
    assert snap_start(moment, "minute") == datetime(2026, 10, 1, 14, 30, tzinfo=UTC)
    assert snap_start(moment, "second") == datetime(2026, 10, 1, 14, 30, 45, tzinfo=UTC)

    assert snap_end_exclusive(moment, "date") == datetime(2026, 10, 2, tzinfo=UTC)
    assert snap_end_exclusive(moment, "hour") == datetime(2026, 10, 1, 15, tzinfo=UTC)
    assert snap_end_exclusive(moment, "minute") == datetime(2026, 10, 1, 14, 31, tzinfo=UTC)
    assert snap_end_exclusive(moment, "second") == datetime(2026, 10, 1, 14, 30, 46, tzinfo=UTC)


def test_resolve_window_date_precision():
    window = resolve_window("2026-10-01", "2026-10-17", None, UTC, now=NOW)
    assert window.start_ms == ms(datetime(2026, 10, 1, tzinfo=UTC))
    assert window.end_ms == ms(datetime(2026, 10, 18, tzinfo=UTC))


def test_resolve_window_minute_precision():
    window = resolve_window("2026-10-01T14:30", "2026-10-01T16:00", None, UTC, now=NOW)
    assert window.start_ms == ms(datetime(2026, 10, 1, 14, 30, tzinfo=UTC))
    assert window.end_ms == ms(datetime(2026, 10, 1, 16, 1, tzinfo=UTC))


def test_resolve_window_to_defaults_to_now():
    window = resolve_window("2026-10-01", None, None, UTC, now=NOW)
    assert window.end_ms == ms(NOW)


def test_resolve_window_last():
    window = resolve_window(None, None, "7d", UTC, now=NOW)
    assert window.start_ms == ms(NOW - timedelta(days=7))
    assert window.end_ms == ms(NOW) + 1
    assert "last 7d" in window.label


def test_resolve_window_errors():
    with pytest.raises(UsageError):
        resolve_window(None, None, None, UTC, now=NOW)
    with pytest.raises(UsageError):
        resolve_window("2026-10-01", None, "7d", UTC, now=NOW)
    with pytest.raises(UsageError):
        resolve_window("2026-10-10", "2026-10-01", None, UTC, now=NOW)


def test_parse_duration_units():
    assert parse_duration("90m") == timedelta(minutes=90)
    assert parse_duration("36h") == timedelta(hours=36)
    assert parse_duration("7d") == timedelta(days=7)
    assert parse_duration("2w") == timedelta(weeks=2)


@pytest.mark.parametrize("text", ["0d", "7", "-1d", "1y", "", "1.5h"])
def test_parse_duration_rejects_invalid(text):
    with pytest.raises(UsageError):
        parse_duration(text)


def test_parse_context_before():
    assert parse_context_before("0") == timedelta(0)
    assert parse_context_before("0m") == timedelta(0)
    assert parse_context_before("") == timedelta(0)
    assert parse_context_before("30m") == timedelta(minutes=30)
    assert parse_context_before("36h") == timedelta(hours=36)
    assert parse_context_before("7d") == timedelta(days=7)
    assert parse_context_before("2w") == timedelta(weeks=2)


@pytest.mark.parametrize("text", ["-1d", "7", "1y", "1.5h", "hello"])
def test_parse_context_before_rejects_invalid(text):
    with pytest.raises(UsageError):
        parse_context_before(text)


def test_parse_tz():
    assert str(parse_tz("UTC")) == "UTC"
    with pytest.raises(UsageError):
        parse_tz("Not/AZone")


class _Args:
    dropbox_token = None
    dropbox_refresh_token = None
    dropbox_app_key = None
    dropbox_app_secret = None
    key = None
    account = None
    folder = None
    tz = "UTC"
    timeout = 30.0
    max_parts = 1000


def _env(monkeypatch, **values):
    for name in (
        "WHATSDROP_DROPBOX_TOKEN",
        "WHATSDROP_DROPBOX_REFRESH_TOKEN",
        "WHATSDROP_DROPBOX_APP_KEY",
        "WHATSDROP_DROPBOX_APP_SECRET",
        "WHATSDROP_KEY",
        "WHATSDROP_ACCOUNT",
        "WHATSDROP_FOLDER",
        "WHATSDROP_TEST_DROPBOX_BASE",
        "WHATSDROP_CREDENTIALS",
    ):
        monkeypatch.delenv(name, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_load_config_from_env(monkeypatch):
    _env(
        monkeypatch,
        WHATSDROP_DROPBOX_TOKEN="token-123",
        WHATSDROP_KEY="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        WHATSDROP_ACCOUNT="primary",
        WHATSDROP_FOLDER="/backup/",
    )
    cfg = load_config(_Args())
    assert cfg.token == "token-123"
    assert cfg.account == "primary"
    assert cfg.folder == "/backup"
    assert cfg.account_root == "/backup/account=primary"


def test_load_config_refresh_credentials(monkeypatch):
    _env(
        monkeypatch,
        WHATSDROP_DROPBOX_REFRESH_TOKEN="refresh-123",
        WHATSDROP_DROPBOX_APP_KEY="app-key",
        WHATSDROP_KEY="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
    )
    cfg = load_config(_Args())
    assert cfg.refresh_token == "refresh-123"
    assert cfg.app_key == "app-key"
    assert cfg.account == "primary"
    assert cfg.folder == ""


def test_resolve_auth_prefers_credentials_app_key(monkeypatch, tmp_path):
    path = tmp_path / "creds.json"
    save_credentials(
        path,
        Credentials(app_key="custom-key", refresh_token="rt", access_token="at"),
    )
    _env(monkeypatch, WHATSDROP_CREDENTIALS=str(path))

    auth = resolve_auth(_Args())

    assert auth.app_key == "custom-key"
    assert auth.token == "at"
    assert auth.from_file is True


def test_resolve_auth_uses_builtin_app_key(monkeypatch, tmp_path):
    _env(monkeypatch, WHATSDROP_CREDENTIALS=str(tmp_path / "missing.json"))

    auth = resolve_auth(_Args())

    assert auth.app_key == DEFAULT_APP_KEY


def test_resolve_auth_explicit_app_key_wins(monkeypatch, tmp_path):
    path = tmp_path / "creds.json"
    save_credentials(
        path,
        Credentials(app_key="file-key", refresh_token="rt", access_token="at"),
    )
    _env(
        monkeypatch,
        WHATSDROP_CREDENTIALS=str(path),
        WHATSDROP_DROPBOX_APP_KEY="explicit-key",
    )

    auth = resolve_auth(_Args())

    assert auth.app_key == "explicit-key"


def test_load_config_requires_credentials(monkeypatch, tmp_path):
    _env(
        monkeypatch,
        WHATSDROP_KEY="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        WHATSDROP_CREDENTIALS=str(tmp_path / "missing.json"),
    )
    with pytest.raises(UsageError, match="not connected"):
        load_config(_Args())


def test_load_config_accepts_missing_key(monkeypatch):
    _env(monkeypatch, WHATSDROP_DROPBOX_TOKEN="token-123")
    cfg = load_config(_Args())
    assert cfg.archive_key == ""


def test_load_config_preserves_key(monkeypatch):
    _env(monkeypatch, WHATSDROP_DROPBOX_TOKEN="token-123", WHATSDROP_KEY="not-a-key")
    cfg = load_config(_Args())
    assert cfg.archive_key == "not-a-key"


def test_load_config_rejects_bad_account(monkeypatch):
    _env(
        monkeypatch,
        WHATSDROP_DROPBOX_TOKEN="token-123",
        WHATSDROP_KEY="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        WHATSDROP_ACCOUNT="bad/account",
    )
    with pytest.raises(UsageError, match="account alias"):
        load_config(_Args())
