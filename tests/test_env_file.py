"""Private env file parsing, permissions and updates."""

from __future__ import annotations

import os
import stat

import pytest

from whatsdrop_cli.env_file import (
    default_env_path,
    load_env_file,
    parse_env_text,
    read_env_file_unchecked,
    write_env_file,
)
from whatsdrop_cli.errors import UsageError

KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


def test_parse_env_text_ignores_comments_and_non_whatsdrop_keys():
    text = "\n".join(
        [
            "# comment",
            "",
            "WHATSDROP_KEY=abc",
            "export WHATSDROP_ACCOUNT=work",
            'WHATSDROP_QUOTED="value with spaces"',
            "WHATSDROP_WITH_EQUALS=a=b",
            "PYTHONPATH=/tmp/evil",
        ]
    )

    values = parse_env_text(text)

    assert values == {
        "WHATSDROP_KEY": "abc",
        "WHATSDROP_ACCOUNT": "work",
        "WHATSDROP_QUOTED": "value with spaces",
        "WHATSDROP_WITH_EQUALS": "a=b",
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_write_env_file_creates_private_file_and_directory(tmp_path):
    path = tmp_path / "whatsdrop" / "env"

    write_env_file(path, key=KEY, account="primary")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path.read_text(encoding="utf-8").splitlines() == [
        "# WhatsDrop CLI environment; keep this file private (chmod 600).",
        f"WHATSDROP_KEY={KEY}",
        "WHATSDROP_ACCOUNT=primary",
    ]
    assert load_env_file(path) == {"WHATSDROP_KEY": KEY, "WHATSDROP_ACCOUNT": "primary"}


def test_write_env_file_preserves_unrelated_entries(tmp_path):
    path = tmp_path / "env"
    path.write_text(
        "\n".join(
            [
                "# keep this comment",
                "WHATSDROP_FOLDER=/archive",
                "WHATSDROP_KEY=old-key",
                "export WHATSDROP_ACCOUNT=old-account",
                "PYTHONPATH=/tmp/evil",
                "",
            ]
        ),
        encoding="utf-8",
    )

    write_env_file(path, key=KEY, account="work")

    text = path.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert "WHATSDROP_FOLDER=/archive" in text
    assert "PYTHONPATH=/tmp/evil" in text
    assert text.count("WHATSDROP_KEY=") == 1
    assert text.count("WHATSDROP_ACCOUNT=") == 1
    assert text.endswith("\n")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_write_env_file_repairs_insecure_mode(tmp_path):
    path = tmp_path / "env"
    path.write_text("WHATSDROP_KEY=old\n", encoding="utf-8")
    path.chmod(0o644)

    write_env_file(path, key=KEY, account="primary")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_env_file(path)["WHATSDROP_KEY"] == KEY


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_read_env_file_unchecked_ignores_insecure_mode(tmp_path):
    path = tmp_path / "env"
    path.write_text("WHATSDROP_KEY=abc\n", encoding="utf-8")
    path.chmod(0o644)

    assert read_env_file_unchecked(path) == {"WHATSDROP_KEY": "abc"}
    with pytest.raises(UsageError, match="must not be readable or writable"):
        load_env_file(path)


def test_default_env_path_uses_credentials_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSDROP_CREDENTIALS", str(tmp_path / "credentials.json"))

    assert default_env_path() == tmp_path / "env"
