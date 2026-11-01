"""End-to-end CLI behaviour against the fake Dropbox archive."""

from __future__ import annotations

import json

import pytest
from conftest import ACCOUNT, APP_KEY, KEY, REFRESH_TOKEN, TOKEN

from whatsdrop_cli.auth import Credentials, save_credentials
from whatsdrop_cli.cli import main
from whatsdrop_cli.crypto import chat_hash, derive_keys


def _configure(monkeypatch, api, token=TOKEN, key=KEY):
    monkeypatch.setenv("WHATSDROP_DROPBOX_TOKEN", token)
    monkeypatch.setenv("WHATSDROP_KEY", key)
    monkeypatch.setenv("WHATSDROP_ACCOUNT", ACCOUNT)
    monkeypatch.setenv("WHATSDROP_TEST_DROPBOX_BASE", api.endpoint)


def _configure_plaintext(monkeypatch, api):
    monkeypatch.setenv("WHATSDROP_DROPBOX_TOKEN", TOKEN)
    monkeypatch.setenv("WHATSDROP_ACCOUNT", ACCOUNT)
    monkeypatch.setenv("WHATSDROP_TEST_DROPBOX_BASE", api.endpoint)
    monkeypatch.delenv("WHATSDROP_KEY", raising=False)


def _query(*extra, tz="UTC"):
    return main(["query", "--tz", tz, "--quiet", *extra])


def _context(*extra, tz="UTC"):
    return main(["context", "--tz", tz, "--quiet", *extra])


def test_query_ndjson_by_name(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _query("--name", "*Anna*", "--from", "2026-10-16", "--to", "2026-10-17")

    assert code == 0
    lines = capsys.readouterr().out.strip().splitlines()
    records = [json.loads(line) for line in lines]
    assert [record["msgId"] for record in records] == ["A1", "A2"]
    assert records[1]["text"] == "repaired copy"


def test_query_by_number(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _query("--number", "+49 171 234567", "--from", "2026-10-17", "--to", "2026-10-17")

    assert code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [record["msgId"] for record in records] == ["A2"]


def test_query_csv_escapes(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _query(
        "--name", "Family*", "--from", "2026-10-17", "--to", "2026-10-17", "--format", "csv"
    )

    assert code == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith("msgId,ts,chatName")
    assert len(lines) == 2
    assert '"Hi, family"' in lines[1]


def test_query_text_format(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _query(
        "--name", "*Bob*", "--from", "2026-10-17", "--to", "2026-10-17", "--format", "text"
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "Bob LID / Bob: lid message" in output
    assert "←" in output


def test_query_to_file(seeded, monkeypatch, capsys, tmp_path):
    _configure(monkeypatch, seeded)
    target = tmp_path / "anna.ndjson"
    code = _query(
        "--name", "*Anna*", "--from", "2026-10-17", "--to", "2026-10-17", "--out", str(target)
    )

    assert code == 0
    assert capsys.readouterr().out == ""
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["msgId"] == "A2"


def test_query_last_is_accepted(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _query("--name", "*Anna*", "--last", "800w")

    assert code == 0
    assert capsys.readouterr().out.strip()


def test_chats_command_lists_everything(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = main(["chats", "--quiet"])

    assert code == 0
    output = capsys.readouterr().out
    assert "Anna Schmidt" in output
    assert "Bob LID" in output
    assert "Family Group" in output


def test_chats_command_filtered(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = main(["chats", "--name", "*Anna*", "--quiet"])

    assert code == 0
    output = capsys.readouterr().out
    assert "Anna Schmidt" in output
    assert "Bob LID" not in output


def test_chats_command_unknown_jid(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)

    code = main(["chats", "--jid", "49111111111@c.us", "--quiet"])

    assert code == 0
    hash_key, _ = derive_keys(KEY)
    assert chat_hash(hash_key, "49111111111@c.us") in capsys.readouterr().out


def test_no_matches_exit_code(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _query("--name", "zzz*", "--from", "2026-10-17", "--to", "2026-10-17")

    assert code == 3
    assert "no chats matched" in capsys.readouterr().err


def test_usage_error_without_window(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _query("--name", "*Anna*")

    assert code == 2
    assert "--from or --last" in capsys.readouterr().err


def test_usage_error_last_with_from(seeded, monkeypatch):
    _configure(monkeypatch, seeded)
    code = _query("--name", "*Anna*", "--from", "2026-10-01", "--last", "7d")

    assert code == 2


def test_guard_exit_code(seeded, monkeypatch):
    _configure(monkeypatch, seeded)
    code = main(
        [
            "query",
            "--name",
            "*Anna*",
            "--from",
            "2026-10-16",
            "--to",
            "2026-10-17",
            "--tz",
            "UTC",
            "--max-parts",
            "1",
            "--quiet",
        ]
    )

    assert code == 4


def test_empty_window_exit_code(seeded, monkeypatch):
    _configure(monkeypatch, seeded)
    code = _query("--name", "*Anna*", "--from", "2020-01-01", "--to", "2020-01-02")

    assert code == 3


def test_all_messages_within_broad_window(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _query("--name", "*", "--from", "2026-10-16", "--to", "2026-10-17")

    assert code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert {record["msgId"] for record in records} == {"A1", "A2", "L1", "G1"}


def test_invalid_token(seeded, monkeypatch):
    _configure(monkeypatch, seeded, token="wrong-token")
    code = main(["chats", "--quiet"])

    assert code == 5


def test_missing_encryption_key(seeded, monkeypatch, capsys):
    monkeypatch.setenv("WHATSDROP_DROPBOX_TOKEN", TOKEN)
    monkeypatch.setenv("WHATSDROP_TEST_DROPBOX_BASE", seeded.endpoint)
    monkeypatch.delenv("WHATSDROP_KEY", raising=False)

    code = main(["chats", "--quiet"])

    assert code == 2
    assert "encryption key required" in capsys.readouterr().err


def test_wrong_encryption_key(seeded, monkeypatch):
    _configure(monkeypatch, seeded, key="BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc=")
    code = main(["chats", "--quiet"])

    assert code == 5


def test_query_plaintext_without_key(seeded_plaintext, monkeypatch, capsys):
    _configure_plaintext(monkeypatch, seeded_plaintext)
    code = _query("--name", "*Anna*", "--from", "2026-10-16", "--to", "2026-10-17")

    assert code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [record["msgId"] for record in records] == ["A1", "A2"]
    assert records[1]["text"] == "repaired copy"


def test_chats_plaintext_warning_is_not_suppressed_by_quiet(seeded_plaintext, monkeypatch, capsys):
    _configure_plaintext(monkeypatch, seeded_plaintext)
    code = main(["chats", "--quiet"])

    assert code == 0
    captured = capsys.readouterr()
    assert "Anna Schmidt" in captured.out
    assert "archive is unencrypted (plaintext)" in captured.err


def test_plaintext_ignores_a_valid_key(seeded_plaintext, monkeypatch, capsys):
    _configure(monkeypatch, seeded_plaintext, key=KEY)
    code = _query("--name", "*Anna*", "--from", "2026-10-17", "--to", "2026-10-17")

    assert code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [record["msgId"] for record in records] == ["A2"]


def test_legacy_archive_without_format_marker(api, monkeypatch, capsys):
    from conftest import seed_archive

    seed_archive(api, format_marker=False)
    _configure(monkeypatch, api)
    code = _query("--name", "*Anna*", "--from", "2026-10-17", "--to", "2026-10-17")

    assert code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [record["msgId"] for record in records] == ["A2"]


def test_query_all_chats_without_selectors(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _query("--from", "2026-10-16", "--to", "2026-10-17")

    assert code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert {record["msgId"] for record in records} == {"A1", "A2", "L1", "G1"}
    assert next(record for record in records if record["msgId"] == "A2")["text"] == "repaired copy"


def test_query_all_chats_empty_window(seeded, monkeypatch):
    _configure(monkeypatch, seeded)
    code = _query("--from", "2020-01-01", "--to", "2020-01-02")

    assert code == 3


def test_midnight_window_downloads_only_overlapping_parts(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _query(
        "--name",
        "*Anna*",
        "--from",
        "2026-10-16 23:30",
        "--to",
        "2026-10-17 00:30",
    )

    assert code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [record["msgId"] for record in records] == ["A1"]
    part_downloads = [key for key in seeded.download_calls if "/chat=" in key]
    assert len(part_downloads) == 1, part_downloads


def test_context_plaintext_without_key(seeded_plaintext, monkeypatch, capsys):
    _configure_plaintext(monkeypatch, seeded_plaintext)
    code = _context("--from", "2026-10-16", "--to", "2026-10-17")

    assert code == 0
    chunks = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert {chunk["chat"]["name"] for chunk in chunks} == {
        "Anna Schmidt",
        "Bob LID",
        "Family Group",
    }


def test_context_groups_by_chat(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _context("--from", "2026-10-16", "--to", "2026-10-17")

    assert code == 0
    chunks = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert {chunk["chat"]["name"] for chunk in chunks} == {
        "Anna Schmidt",
        "Bob LID",
        "Family Group",
    }
    assert all(chunk["parts"] == 1 for chunk in chunks)
    anna = next(chunk for chunk in chunks if chunk["chat"]["name"] == "Anna Schmidt")
    assert [message["msgId"] for message in anna["messages"]] == ["A1", "A2"]
    assert all(message["inWindow"] for message in anna["messages"])
    assert anna["contextBeforeMs"] == 0


def test_context_default_omits_pre_window_messages(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _context("--name", "*Anna*", "--from", "2026-10-17", "--to", "2026-10-17")

    assert code == 0
    chunks = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert len(chunks) == 1
    assert [message["msgId"] for message in chunks[0]["messages"]] == ["A2"]
    assert chunks[0]["contextBeforeMs"] == 0


def test_context_before_includes_pre_window_messages(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _context(
        "--name",
        "*Anna*",
        "--from",
        "2026-10-17",
        "--to",
        "2026-10-17",
        "--context-before",
        "1d",
    )

    assert code == 0
    chunks = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert len(chunks) == 1
    assert [message["msgId"] for message in chunks[0]["messages"]] == ["A1", "A2"]
    assert [message["inWindow"] for message in chunks[0]["messages"]] == [False, True]
    assert chunks[0]["contextBeforeMs"] == 86_400_000


def test_context_only_window_exits_3(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _context(
        "--name",
        "*Anna*",
        "--from",
        "2026-10-17 00:00",
        "--to",
        "2026-10-17 00:01",
        "--context-before",
        "1d",
    )

    assert code == 3
    assert "analysis window" in capsys.readouterr().err


def test_context_text_format(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _context(
        "--name",
        "*Anna*",
        "--from",
        "2026-10-17",
        "--to",
        "2026-10-17",
        "--context-before",
        "1d",
        "--format",
        "text",
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "=== CHAT" in output
    assert "Anna Schmidt" in output
    assert "[context]" in output
    assert "late message" in output
    assert "repaired copy" in output
    assert "=== END CHAT (1/1) ===" in output


def test_context_max_chars_splits_at_message_boundaries(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _context(
        "--name",
        "*Anna*",
        "--from",
        "2026-10-16",
        "--to",
        "2026-10-17",
        "--max-chars",
        "60",
    )

    assert code == 0
    chunks = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [chunk["part"] for chunk in chunks] == [1, 2]
    assert all(chunk["parts"] == 2 for chunk in chunks)
    assert all(chunk["oversized"] is False for chunk in chunks)
    assert [message["msgId"] for chunk in chunks for message in chunk["messages"]] == ["A1", "A2"]


def test_context_to_file(seeded, monkeypatch, capsys, tmp_path):
    _configure(monkeypatch, seeded)
    target = tmp_path / "context.ndjson"
    code = _context(
        "--name",
        "*Anna*",
        "--from",
        "2026-10-17",
        "--to",
        "2026-10-17",
        "--out",
        str(target),
    )

    assert code == 0
    assert capsys.readouterr().out == ""
    chunks = [json.loads(line) for line in target.read_text(encoding="utf-8").strip().splitlines()]
    assert len(chunks) == 1
    assert chunks[0]["messages"][0]["msgId"] == "A2"


def test_context_negative_max_chars(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _context(
        "--name",
        "*Anna*",
        "--from",
        "2026-10-17",
        "--to",
        "2026-10-17",
        "--max-chars",
        "-1",
    )

    assert code == 2
    assert "--max-chars" in capsys.readouterr().err


def test_context_no_chats_matched(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _context("--name", "zzz*", "--from", "2026-10-17", "--to", "2026-10-17")

    assert code == 3
    assert "no chats matched" in capsys.readouterr().err


def test_context_requires_window(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _context("--name", "*Anna*")

    assert code == 2
    assert "--from or --last" in capsys.readouterr().err


def test_context_last_is_accepted(seeded, monkeypatch, capsys):
    _configure(monkeypatch, seeded)
    code = _context("--name", "*Anna*", "--last", "800w")

    assert code == 0
    assert capsys.readouterr().out.strip()


def test_version(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--version"])

    assert error.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def _auth_credentials(tmp_path):
    path = tmp_path / "creds.json"
    save_credentials(
        path,
        Credentials(
            app_key=APP_KEY,
            refresh_token=REFRESH_TOKEN,
            access_token=TOKEN,
        ),
    )
    return path


def _configure_auth(monkeypatch, api, path):
    monkeypatch.delenv("WHATSDROP_DROPBOX_TOKEN", raising=False)
    monkeypatch.delenv("WHATSDROP_DROPBOX_REFRESH_TOKEN", raising=False)
    monkeypatch.setenv("WHATSDROP_CREDENTIALS", str(path))
    monkeypatch.setenv("WHATSDROP_TEST_DROPBOX_BASE", api.endpoint)


def test_revoke_keeps_credentials_on_remote_error(seeded, monkeypatch, tmp_path, capsys):
    path = _auth_credentials(tmp_path)
    _configure_auth(monkeypatch, seeded, path)
    seeded.override = (500, {"error": "boom"})

    code = main(["auth", "--revoke"])

    assert code == 5
    assert path.exists()
    assert "could not revoke" in capsys.readouterr().err


def test_revoke_deletes_credentials_on_success(seeded, monkeypatch, tmp_path, capsys):
    path = _auth_credentials(tmp_path)
    _configure_auth(monkeypatch, seeded, path)

    code = main(["auth", "--revoke"])

    assert code == 0
    assert not path.exists()
    assert "revoked Dropbox access" in capsys.readouterr().out


def test_revoke_deletes_credentials_when_token_already_invalid(
    seeded, monkeypatch, tmp_path, capsys
):
    path = _auth_credentials(tmp_path)
    _configure_auth(monkeypatch, seeded, path)
    seeded.override = (401, {"error": "invalid_access_token"})

    code = main(["auth", "--revoke"])

    assert code == 0
    assert not path.exists()
    assert "already invalid" in capsys.readouterr().err
