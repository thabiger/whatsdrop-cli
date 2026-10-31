"""Output rendering, escaping and file permission tests."""

from __future__ import annotations

import stat
from datetime import timezone

from whatsdrop_cli.media import write_manifest
from whatsdrop_cli.output import render_csv, render_text, write_lines


def make_record(**overrides) -> dict:
    record = {
        "msgId": "m1",
        "ts": 1_792_228_800_000,
        "chatName": "Alpha",
        "chatPhone": "15550100",
        "senderName": "Bob",
        "direction": "in",
        "type": "chat",
        "text": "hello",
    }
    record.update(overrides)
    return record


def test_render_text_escapes_control_characters():
    record = make_record(
        chatName="bad\x1b[31m",
        senderName="s\x00",
        text="line1\nline2\x07",
    )

    output = "\n".join(render_text([record], timezone.utc))

    assert "\x1b" not in output
    assert "\x00" not in output
    assert "\x07" not in output
    assert "line1\\nline2" in output


def test_render_csv_prefixes_formula_cells():
    record = make_record(text='=HYPERLINK("https://example.com")')

    lines = render_csv([record], timezone.utc)

    assert "'=HYPERLINK" in lines[1]


def test_write_lines_sets_private_permissions(tmp_path):
    target = tmp_path / "out.ndjson"
    write_lines(["line"], str(target))
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_manifest_sets_private_permissions(tmp_path):
    target = tmp_path / "manifest.ndjson"
    write_manifest(target, [{"msgId": "m1"}])
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
