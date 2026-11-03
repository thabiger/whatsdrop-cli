"""The `whatsdrop config` command."""

from __future__ import annotations

import os
import stat
import sys

import pytest
from conftest import KEY

from whatsdrop_cli.cli import main
from whatsdrop_cli.env_file import load_env_file


def _isolate(monkeypatch, tmp_path):
    for name in ("WHATSDROP_KEY", "WHATSDROP_ACCOUNT", "WHATSDROP_CREDENTIALS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WHATSDROP_CREDENTIALS", str(tmp_path / "credentials.json"))


def _raise_eof(prompt=""):
    raise EOFError


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_config_writes_key_and_account(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)

    code = main(["config", "--key", KEY, "--account", "work"])

    assert code == 0
    path = tmp_path / "env"
    assert load_env_file(path) == {"WHATSDROP_KEY": KEY, "WHATSDROP_ACCOUNT": "work"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    output = capsys.readouterr().out
    assert str(path) in output
    assert KEY not in output


def test_config_uses_environment_key(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSDROP_KEY", KEY)

    code = main(["config", "--account", "work"])

    assert code == 0
    assert load_env_file(tmp_path / "env") == {
        "WHATSDROP_KEY": KEY,
        "WHATSDROP_ACCOUNT": "work",
    }
    assert KEY not in capsys.readouterr().out


def test_config_prompts_for_key(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("whatsdrop_cli.cli.getpass.getpass", lambda prompt="": KEY)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    code = main(["config"])

    assert code == 0
    assert load_env_file(tmp_path / "env") == {
        "WHATSDROP_KEY": KEY,
        "WHATSDROP_ACCOUNT": "primary",
    }


def test_config_prompts_for_account_when_unset(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSDROP_KEY", KEY)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "work")

    code = main(["config"])

    assert code == 0
    assert load_env_file(tmp_path / "env") == {
        "WHATSDROP_KEY": KEY,
        "WHATSDROP_ACCOUNT": "work",
    }


def test_config_empty_account_prompt_uses_primary(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSDROP_KEY", KEY)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    code = main(["config"])

    assert code == 0
    assert load_env_file(tmp_path / "env")["WHATSDROP_ACCOUNT"] == "primary"


def test_config_aborts_on_account_prompt_eof(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSDROP_KEY", KEY)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _raise_eof)

    code = main(["config"])

    assert code == 2
    assert not (tmp_path / "env").exists()
    assert "account alias prompt aborted" in capsys.readouterr().err


def test_config_aborts_on_key_prompt_eof(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("whatsdrop_cli.cli.getpass.getpass", _raise_eof)

    code = main(["config"])

    assert code == 2
    assert not (tmp_path / "env").exists()
    assert "archive key prompt aborted" in capsys.readouterr().err


def test_config_rejects_invalid_prompted_account(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSDROP_KEY", KEY)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "bad alias")

    code = main(["config"])

    assert code == 2
    assert not (tmp_path / "env").exists()
    assert "account alias" in capsys.readouterr().err


def test_config_account_defaults_to_primary_when_not_interactive(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSDROP_KEY", KEY)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    code = main(["config"])

    assert code == 0
    assert load_env_file(tmp_path / "env")["WHATSDROP_ACCOUNT"] == "primary"


def _fail_input(prompt=""):
    raise AssertionError("account prompt should not appear")


def test_config_skips_account_prompt_for_flag(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSDROP_KEY", KEY)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _fail_input)

    code = main(["config", "--account", "work"])

    assert code == 0
    assert load_env_file(tmp_path / "env")["WHATSDROP_ACCOUNT"] == "work"


def test_config_skips_account_prompt_for_environment(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSDROP_KEY", KEY)
    monkeypatch.setenv("WHATSDROP_ACCOUNT", "work")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _fail_input)

    code = main(["config"])

    assert code == 0
    assert load_env_file(tmp_path / "env")["WHATSDROP_ACCOUNT"] == "work"


def test_config_requires_key_when_not_interactive(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    code = main(["config"])

    assert code == 2
    assert not (tmp_path / "env").exists()
    assert "archive key is required" in capsys.readouterr().err


def test_config_rejects_invalid_key(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)

    code = main(["config", "--key", "not-base64"])

    assert code == 2
    assert not (tmp_path / "env").exists()
    assert "32 bytes encoded as base64" in capsys.readouterr().err


def test_config_rejects_invalid_account(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)

    code = main(["config", "--key", KEY, "--account", "bad alias"])

    assert code == 2
    assert not (tmp_path / "env").exists()
    assert "account alias" in capsys.readouterr().err


def test_config_keeps_existing_values_on_empty_prompts(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "env"
    path.write_text(f"WHATSDROP_ACCOUNT=work\nWHATSDROP_KEY={KEY}\n", encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def keep_key(prompt=""):
        assert "keep existing" in prompt
        assert KEY not in prompt
        return ""

    def keep_account(prompt=""):
        assert "[work]" in prompt
        return ""

    monkeypatch.setattr("whatsdrop_cli.cli.getpass.getpass", keep_key)
    monkeypatch.setattr("builtins.input", keep_account)

    code = main(["config"])

    assert code == 0
    assert load_env_file(path) == {"WHATSDROP_KEY": KEY, "WHATSDROP_ACCOUNT": "work"}


def test_config_can_change_existing_values(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "env"
    path.write_text(f"WHATSDROP_ACCOUNT=work\nWHATSDROP_KEY={KEY}\n", encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        "whatsdrop_cli.cli.getpass.getpass",
        lambda prompt="": "YmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmI=",
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "new-account")

    code = main(["config"])

    assert code == 0
    assert load_env_file(path) == {
        "WHATSDROP_KEY": "YmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmI=",
        "WHATSDROP_ACCOUNT": "new-account",
    }


def test_config_keeps_existing_values_when_not_interactive_on_rerun(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "env"
    path.write_text(f"WHATSDROP_ACCOUNT=work\nWHATSDROP_KEY={KEY}\n", encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    code = main(["config"])

    assert code == 0
    assert load_env_file(path) == {"WHATSDROP_KEY": KEY, "WHATSDROP_ACCOUNT": "work"}


def test_config_prompts_fresh_when_existing_key_is_invalid(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "env"
    path.write_text("WHATSDROP_ACCOUNT=work\nWHATSDROP_KEY=not-base64\n", encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def fresh_key(prompt=""):
        assert "keep existing" not in prompt
        return KEY

    monkeypatch.setattr("whatsdrop_cli.cli.getpass.getpass", fresh_key)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    code = main(["config"])

    assert code == 0
    assert load_env_file(path) == {"WHATSDROP_KEY": KEY, "WHATSDROP_ACCOUNT": "work"}


def test_config_custom_env_file(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "custom" / "env"

    code = main(["config", "--key", KEY, "--env-file", str(path)])

    assert code == 0
    assert load_env_file(path) == {
        "WHATSDROP_KEY": KEY,
        "WHATSDROP_ACCOUNT": "primary",
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_config_repairs_insecure_env_file(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "env"
    path.write_text("WHATSDROP_KEY=old\n", encoding="utf-8")
    path.chmod(0o644)

    code = main(["config", "--key", KEY, "--account", "work"])

    assert code == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_env_file(path) == {"WHATSDROP_KEY": KEY, "WHATSDROP_ACCOUNT": "work"}
