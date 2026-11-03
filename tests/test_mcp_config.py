"""MCP settings, environment file parsing and precedence."""

from __future__ import annotations

import json
import os

import pytest

from whatsdrop_cli.errors import UsageError
from whatsdrop_cli.mcp_config import (
    DEFAULT_CLI_TIMEOUT_SECONDS,
    DEFAULT_CONTEXT_BEFORE,
    DEFAULT_LAST,
    DEFAULT_MAX_CHARS_PER_CHAT,
    DEFAULT_MAX_CHATS,
    DEFAULT_MAX_PARTS,
    DEFAULT_MAX_TOTAL_CHARS,
    McpSettings,
    default_config_path,
    load_env_file,
    load_settings,
)

MCP_ENV_VARS = [
    "WHATSDROP_MCP_CONFIG",
    "WHATSDROP_MCP_DEFAULT_LAST",
    "WHATSDROP_MCP_DEFAULT_CONTEXT_BEFORE",
    "WHATSDROP_MCP_MAX_CHARS",
    "WHATSDROP_MCP_MAX_TOTAL_CHARS",
    "WHATSDROP_MCP_MAX_CHATS",
    "WHATSDROP_MCP_MAX_PARTS",
    "WHATSDROP_MCP_TIMEOUT",
    "WHATSDROP_MCP_ENV_FILE",
]


def _prepare(monkeypatch, tmp_path):
    for name in MCP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setenv("WHATSDROP_CREDENTIALS", str(tmp_path / "credentials.json"))


def _write_config(tmp_path, document):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_defaults(tmp_path, monkeypatch):
    _prepare(monkeypatch, tmp_path)

    settings = load_settings()

    assert settings.default_last == DEFAULT_LAST
    assert settings.default_context_before == DEFAULT_CONTEXT_BEFORE
    assert settings.max_chars_per_chat == DEFAULT_MAX_CHARS_PER_CHAT
    assert settings.max_total_chars == DEFAULT_MAX_TOTAL_CHARS
    assert settings.max_chats == DEFAULT_MAX_CHATS
    assert settings.max_parts == DEFAULT_MAX_PARTS
    assert settings.cli_timeout_seconds == DEFAULT_CLI_TIMEOUT_SECONDS
    assert settings.account == "primary"
    assert settings.env_path == tmp_path / "env"


def test_config_file_values(tmp_path, monkeypatch):
    _prepare(monkeypatch, tmp_path)
    path = _write_config(
        tmp_path,
        {
            "version": 1,
            "default_last": "24h",
            "default_context_before": "1h",
            "max_chars_per_chat": 5000,
            "max_total_chars": 20000,
            "max_chats": 5,
            "max_parts": 100,
            "cli_timeout_seconds": 30,
            "account": "work",
            "env_file": str(tmp_path / "custom.env"),
            "tz": "UTC",
        },
    )
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(path))

    settings = load_settings()

    assert settings.default_last == "24h"
    assert settings.default_context_before == "1h"
    assert settings.max_chars_per_chat == 5000
    assert settings.max_total_chars == 20000
    assert settings.max_chats == 5
    assert settings.max_parts == 100
    assert settings.cli_timeout_seconds == 30
    assert settings.account == "work"
    assert settings.env_path == tmp_path / "custom.env"
    assert settings.tz == "UTC"


def test_environment_overrides_config(tmp_path, monkeypatch):
    _prepare(monkeypatch, tmp_path)
    path = _write_config(
        tmp_path,
        {
            "version": 1,
            "default_last": "24h",
            "max_total_chars": 20000,
            "max_chats": 5,
            "max_parts": 100,
            "cli_timeout_seconds": 30,
        },
    )
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(path))
    monkeypatch.setenv("WHATSDROP_MCP_DEFAULT_LAST", "2h")
    monkeypatch.setenv("WHATSDROP_MCP_MAX_TOTAL_CHARS", "1000")
    monkeypatch.setenv("WHATSDROP_MCP_MAX_CHATS", "2")
    monkeypatch.setenv("WHATSDROP_MCP_MAX_PARTS", "7")
    monkeypatch.setenv("WHATSDROP_MCP_TIMEOUT", "15")

    settings = load_settings()

    assert settings.default_last == "2h"
    assert settings.max_total_chars == 1000
    assert settings.max_chats == 2
    assert settings.max_parts == 7
    assert settings.cli_timeout_seconds == 15


def test_invalid_json(tmp_path, monkeypatch):
    _prepare(monkeypatch, tmp_path)
    path = tmp_path / "mcp.json"
    path.write_text("{", encoding="utf-8")
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(path))

    with pytest.raises(ValueError, match="not valid JSON"):
        load_settings()


def test_wrong_version(tmp_path, monkeypatch):
    _prepare(monkeypatch, tmp_path)
    path = _write_config(tmp_path, {"version": 2})
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(path))

    with pytest.raises(ValueError, match="version"):
        load_settings()


def test_negative_values(tmp_path, monkeypatch):
    _prepare(monkeypatch, tmp_path)
    path = _write_config(tmp_path, {"version": 1, "max_total_chars": -1})
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(path))

    with pytest.raises(ValueError, match="max_total_chars"):
        load_settings()


def test_max_parts_zero(tmp_path, monkeypatch):
    _prepare(monkeypatch, tmp_path)
    path = _write_config(tmp_path, {"version": 1, "max_parts": 0})
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(path))

    with pytest.raises(ValueError, match="max_parts"):
        load_settings()


def test_invalid_default_last(tmp_path, monkeypatch):
    _prepare(monkeypatch, tmp_path)
    path = _write_config(tmp_path, {"version": 1, "default_last": "soon"})
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(path))

    with pytest.raises(ValueError, match="default_last"):
        load_settings()


def test_invalid_account(tmp_path, monkeypatch):
    _prepare(monkeypatch, tmp_path)
    path = _write_config(tmp_path, {"version": 1, "account": "../bad"})
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(path))

    with pytest.raises(ValueError, match="account"):
        load_settings()


def test_env_path_comes_from_credentials_location(tmp_path):
    settings = McpSettings(credentials_file=str(tmp_path / "creds.json"))

    assert settings.env_path == tmp_path / "env"


def test_env_file_override(tmp_path):
    settings = McpSettings(
        credentials_file=str(tmp_path / "creds.json"),
        env_file=str(tmp_path / "other.env"),
    )

    assert settings.env_path == tmp_path / "other.env"


def test_default_config_path_uses_explicit_env(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit.json"
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(explicit))

    assert default_config_path() == explicit


def test_load_env_file(tmp_path):
    path = tmp_path / "env"
    path.write_text(
        "\n".join(
            [
                "# comment",
                "",
                "WHATSDROP_KEY=abc",
                "export WHATSDROP_ACCOUNT=work",
                'WHATSDROP_QUOTED="value with spaces"',
                "WHATSDROP_WITH_EQUALS=a=b",
                "PYTHONPATH=/tmp/evil",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)

    values = load_env_file(path)

    assert values["WHATSDROP_KEY"] == "abc"
    assert values["WHATSDROP_ACCOUNT"] == "work"
    assert values["WHATSDROP_QUOTED"] == "value with spaces"
    assert values["WHATSDROP_WITH_EQUALS"] == "a=b"
    assert "PYTHONPATH" not in values


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_load_env_file_rejects_group_world_readable(tmp_path):
    path = tmp_path / "env"
    path.write_text("WHATSDROP_KEY=abc\n", encoding="utf-8")
    path.chmod(0o644)

    with pytest.raises(UsageError, match="must not be readable or writable"):
        load_env_file(path)


def test_load_env_file_missing(tmp_path):
    assert load_env_file(tmp_path / "missing.env") == {}
