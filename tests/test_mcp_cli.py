"""Subprocess wrapper and output limiting for the MCP server."""

from __future__ import annotations

import os
import subprocess

import pytest
from conftest import ACCOUNT, KEY, TOKEN

from whatsdrop_cli.mcp_cli import (
    CliResult,
    build_env,
    chats_args,
    context_args,
    describe_failure,
    effective_account,
    limit_cli_text,
    run_cli,
)
from whatsdrop_cli.mcp_config import McpSettings
from whatsdrop_cli.mcp_server import run_check


def _configure(monkeypatch, api, token=TOKEN, key=KEY):
    monkeypatch.setenv("WHATSDROP_DROPBOX_TOKEN", token)
    monkeypatch.setenv("WHATSDROP_KEY", key)
    monkeypatch.setenv("WHATSDROP_ACCOUNT", ACCOUNT)
    monkeypatch.setenv("WHATSDROP_TEST_DROPBOX_BASE", api.endpoint)


def _settings(tmp_path, **overrides):
    values = {"env_file": str(tmp_path / "missing.env")}
    values.update(overrides)
    return McpSettings(**values)


def _context_args(**overrides):
    values = {
        "last": "800w",
        "start": None,
        "end": None,
        "context_before": "0",
        "max_chars_per_chat": 0,
        "max_parts": 1000,
        "tz": "UTC",
    }
    values.update(overrides)
    return context_args(**values)


def test_chats_run(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = run_cli(chats_args(), _settings(tmp_path))

    assert result.returncode == 0
    assert "Anna Schmidt" in result.stdout
    assert "Bob LID" in result.stdout
    assert "Family Group" in result.stdout


def test_context_run(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = run_cli(_context_args(), _settings(tmp_path))

    assert result.returncode == 0
    assert "=== CHAT" in result.stdout


def test_context_by_name(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = run_cli(_context_args(names=["*Anna*"]), _settings(tmp_path))

    assert result.returncode == 0
    assert "Anna Schmidt" in result.stdout
    assert "Bob LID" not in result.stdout


def test_missing_auth(monkeypatch, tmp_path):
    monkeypatch.delenv("WHATSDROP_DROPBOX_TOKEN", raising=False)
    monkeypatch.delenv("WHATSDROP_DROPBOX_REFRESH_TOKEN", raising=False)
    monkeypatch.setenv("WHATSDROP_CREDENTIALS", str(tmp_path / "missing.json"))

    result = run_cli(chats_args(), _settings(tmp_path))

    assert result.returncode == 2
    assert "whatsdrop auth" in describe_failure(result, "list chats", 120)


def test_no_matches(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = run_cli(_context_args(names=["zzz*"]), _settings(tmp_path))

    assert result.returncode == 3
    assert "No archived" in describe_failure(result, "fetch context", 120)


def test_max_parts_guard(seeded, monkeypatch, tmp_path):
    _configure(monkeypatch, seeded)

    result = run_cli(_context_args(max_parts=1), _settings(tmp_path))

    assert result.returncode == 4
    assert "Too many archive parts" in describe_failure(result, "fetch context", 120)


def test_timeout(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(["whatsdrop"], 1)

    monkeypatch.setattr("whatsdrop_cli.mcp_cli.subprocess.run", boom)

    result = run_cli(chats_args(), _settings(tmp_path))

    assert result.timed_out is True
    assert result.returncode == -1
    assert "timed out" in describe_failure(result, "list chats", 15)


def test_describe_failure_mapping():
    assert "configuration" in describe_failure(CliResult(2, "", "bad"), "x", 120)
    assert "No archived" in describe_failure(CliResult(3, "", "none"), "x", 120)
    assert "Too many" in describe_failure(CliResult(4, "", "guard"), "x", 120)
    assert "Dropbox" in describe_failure(CliResult(5, "", "remote"), "x", 120)
    assert "start" in describe_failure(CliResult(-1, "", "missing"), "x", 120)
    assert "exit code 9" in describe_failure(CliResult(9, "", "other"), "x", 120)


def test_build_env_does_not_override_process_env(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    env_file.write_text("WHATSDROP_KEY=fromfile\nWHATSDROP_EXTRA=present\n", encoding="utf-8")
    env_file.chmod(0o600)
    monkeypatch.setenv("WHATSDROP_KEY", "fromenv")

    env = build_env(_settings(tmp_path, env_file=str(env_file), account="work"))

    assert env["WHATSDROP_KEY"] == "fromenv"
    assert env["WHATSDROP_EXTRA"] == "present"
    assert env["WHATSDROP_ACCOUNT"] == "work"


def test_effective_account_process_env_wins(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    env_file.write_text("WHATSDROP_ACCOUNT=fromfile\n", encoding="utf-8")
    env_file.chmod(0o600)
    monkeypatch.setenv("WHATSDROP_ACCOUNT", "fromenv")

    account = effective_account(_settings(tmp_path, env_file=str(env_file), account="config"))

    assert account == "fromenv"


def test_effective_account_env_file_wins_over_config(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    env_file.write_text("WHATSDROP_ACCOUNT=fromfile\n", encoding="utf-8")
    env_file.chmod(0o600)
    monkeypatch.delenv("WHATSDROP_ACCOUNT", raising=False)

    account = effective_account(_settings(tmp_path, env_file=str(env_file), account="config"))

    assert account == "fromfile"


def test_effective_account_falls_back_to_config(tmp_path, monkeypatch):
    monkeypatch.delenv("WHATSDROP_ACCOUNT", raising=False)

    account = effective_account(_settings(tmp_path, account="config"))

    assert account == "config"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_run_cli_reports_insecure_env_file(tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text("WHATSDROP_KEY=abc\n", encoding="utf-8")
    env_file.chmod(0o644)

    result = run_cli(chats_args(), _settings(tmp_path, env_file=str(env_file)))

    assert result.returncode == 2
    assert "must not be readable or writable" in describe_failure(result, "list chats", 120)


def _text() -> str:
    return (
        "=== CHAT a | Anna | dm | - ===\n"
        "[window x]\n"
        "message a\n"
        "=== END CHAT (1/1) ===\n"
        "\n"
        "=== CHAT b | Bob | dm | - ===\n"
        "[window x]\n"
        "message b\n"
        "=== END CHAT (1/1) ===\n"
        "\n"
        "=== CHAT c | Cid | dm | - ===\n"
        "[window x]\n"
        "message c\n"
        "=== END CHAT (1/1) ===\n"
    )


def test_limit_text_does_not_split_escaped_chat_name():
    text = (
        "=== CHAT a | Evil\\n=== CHAT fake / Fake / dm / - === | dm | - ===\n"
        "[window x]\n"
        "message a\n"
        "=== END CHAT (1/1) ===\n"
    )

    limited = limit_cli_text(text, max_chats=1, max_total_chars=0)

    assert limited.truncated is False
    assert limited.included_chats == 1
    assert limited.omitted_chats == 0


def test_limit_text_uncapped():
    text = _text()

    limited = limit_cli_text(text, max_chats=0, max_total_chars=0)

    assert limited.text == text
    assert limited.truncated is False
    assert limited.included_chats == 3
    assert limited.omitted_chats == 0


def test_limit_text_max_chats():
    limited = limit_cli_text(_text(), max_chats=1, max_total_chars=0)

    assert "Anna" in limited.text
    assert "Bob" not in limited.text
    assert limited.truncated is True
    assert limited.included_chats == 1
    assert limited.omitted_chats == 2


def test_limit_text_parts_count_as_one_chat():
    text = _text().replace(
        "=== END CHAT (1/1) ===\n",
        "=== END PART 1/2 ===\n\n"
        "=== CHAT a | Anna | dm | - | part 2/2 ===\n"
        "[window x]\n"
        "message a2\n"
        "=== END CHAT (2/2) ===\n",
        1,
    )

    limited = limit_cli_text(text, max_chats=1, max_total_chars=0)

    assert "message a" in limited.text
    assert "message a2" in limited.text
    assert "Bob" not in limited.text
    assert limited.included_chats == 1
    assert limited.omitted_chats == 2


def test_limit_text_total_chars():
    limited = limit_cli_text(_text(), max_chats=0, max_total_chars=120)

    assert limited.truncated is True
    assert "output truncated" in limited.text
    assert len(limited.text) <= 120 + 200


def test_check_against_fake_dropbox(seeded, monkeypatch, tmp_path, capsys):
    _configure(monkeypatch, seeded)

    code = run_check(_settings(tmp_path))

    assert code == 0
    assert "ok; archive reachable" in capsys.readouterr().err
