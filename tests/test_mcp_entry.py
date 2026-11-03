"""MCP entry point, setup helper and health check."""

from __future__ import annotations

import builtins
import os

import pytest

from whatsdrop_cli.mcp_cli import CliResult
from whatsdrop_cli.mcp_config import McpSettings
from whatsdrop_cli.mcp_server import main, print_setup, run_check


def _prepare(monkeypatch, tmp_path):
    monkeypatch.setenv("WHATSDROP_MCP_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setenv("WHATSDROP_CREDENTIALS", str(tmp_path / "credentials.json"))


def _settings(tmp_path):
    return McpSettings(
        env_file=str(tmp_path / "env"),
        credentials_file=str(tmp_path / "credentials.json"),
    )


def test_version(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--version"])

    assert error.value.code == 0
    assert "whatsdrop-mcp" in capsys.readouterr().out


def test_print_setup(tmp_path, monkeypatch, capsys):
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSDROP_ACCOUNT", "work")

    code = main(["--print-setup"])

    assert code == 0
    out = capsys.readouterr().out
    assert '"mcpServers"' in out
    assert '"whatsdrop"' in out
    assert '"work"' in out
    assert "WHATSDROP_KEY" not in out


def test_print_setup_does_not_leak_key(tmp_path, capsys):
    key = "super-secret-archive-key"
    settings = _settings(tmp_path)
    settings.env_path.write_text(f"WHATSDROP_KEY={key}\n", encoding="utf-8")
    settings.env_path.chmod(0o600)

    print_setup(settings)

    captured = capsys.readouterr()
    assert key not in captured.out
    assert key not in captured.err


def test_print_setup_uses_env_file_account(tmp_path, monkeypatch, capsys):
    _prepare(monkeypatch, tmp_path)
    env_file = tmp_path / "env"
    env_file.write_text("WHATSDROP_ACCOUNT=work\n", encoding="utf-8")
    env_file.chmod(0o600)

    code = main(["--print-setup"])

    assert code == 0
    assert '"work"' in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_print_setup_rejects_insecure_env_file(tmp_path, monkeypatch, capsys):
    _prepare(monkeypatch, tmp_path)
    env_file = tmp_path / "env"
    env_file.write_text("WHATSDROP_KEY=abc\n", encoding="utf-8")
    env_file.chmod(0o644)

    code = main(["--print-setup"])

    assert code == 2
    assert "must not be readable or writable" in capsys.readouterr().err


def test_check_uses_env_file_account(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / "env"
    env_file.write_text("WHATSDROP_ACCOUNT=work\n", encoding="utf-8")
    env_file.chmod(0o600)
    monkeypatch.setattr(
        "whatsdrop_cli.mcp_server.run_cli",
        lambda *args, **kwargs: CliResult(0, "NAME PHONE TYPE HASH CHAT ID\n", ""),
    )

    code = run_check(_settings(tmp_path))

    assert code == 0
    assert "account=work" in capsys.readouterr().err


def test_missing_mcp_extra(tmp_path, monkeypatch, capsys):
    _prepare(monkeypatch, tmp_path)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mcp.server":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    code = main([])

    assert code == 2
    assert "MCP dependency" in capsys.readouterr().err


def test_check_success(tmp_path, monkeypatch, capsys):
    results = [
        CliResult(0, '{"ok": true}', ""),
        CliResult(0, "NAME PHONE TYPE HASH CHAT ID\nAnna - dm abc 1@c.us\n", ""),
    ]
    monkeypatch.setattr(
        "whatsdrop_cli.mcp_server.run_cli",
        lambda *args, **kwargs: results.pop(0),
    )

    code = run_check(_settings(tmp_path))

    assert code == 0
    assert "ok; archive reachable" in capsys.readouterr().err


def test_check_auth_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "whatsdrop_cli.mcp_server.run_cli",
        lambda *args, **kwargs: CliResult(2, "", "Dropbox is not connected."),
    )

    code = run_check(_settings(tmp_path))

    assert code == 2
    assert "configuration" in capsys.readouterr().err


def test_check_start_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "whatsdrop_cli.mcp_server.run_cli",
        lambda *args, **kwargs: CliResult(-1, "", "not found"),
    )

    code = run_check(_settings(tmp_path))

    assert code == 2
    assert "Could not start" in capsys.readouterr().err
