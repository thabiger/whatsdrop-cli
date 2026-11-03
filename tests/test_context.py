"""Unit tests for per-chat context grouping, chunking and rendering."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from whatsdrop_cli.chats import ChatEntry
from whatsdrop_cli.config import Window
from whatsdrop_cli.context import (
    attach_media_exports,
    build_chunks,
    group_records,
    render_chunks_ndjson,
    render_chunks_text,
)

UTC = timezone.utc


def make_window(start_ms: int, end_ms: int) -> Window:
    return Window(
        start_ms=start_ms,
        end_ms=end_ms,
        start=datetime.fromtimestamp(start_ms / 1000, tz=UTC),
        end=datetime.fromtimestamp(end_ms / 1000, tz=UTC),
        label="test",
    )


def make_record(
    msg_id: str,
    ts: int,
    *,
    chat_id: str | None = "49171234567@c.us",
    chat_name: str = "Anna Schmidt",
    text: str = "hello",
    direction: str = "in",
    **extra,
) -> dict:
    record = {
        "v": 1,
        "msgId": msg_id,
        "chatId": chat_id,
        "chatName": chat_name,
        "isGroup": bool(chat_id and chat_id.endswith("@g.us")),
        "direction": direction,
        "senderName": "Anna",
        "text": text,
        "ts": ts,
        "capturedAt": ts + 100,
        "type": "chat",
        "source": "store",
        "sortTs": ts,
    }
    record.update(extra)
    return record


def make_entry(
    hash_value: str,
    chat_id: str,
    chat_name: str,
    *,
    phone: str | None = None,
    is_group: bool = False,
) -> ChatEntry:
    return ChatEntry(
        hash=hash_value,
        chat_id=chat_id,
        chat_name=chat_name,
        chat_phone=phone,
        is_group=is_group,
    )


def test_group_records_groups_by_chat_and_marks_context():
    window = make_window(1000, 2000)
    selected = [make_entry("aa", "1@c.us", "A", phone="1")]
    records = [
        make_record("c1", 900, chat_id="1@c.us", chat_name="A", text="before"),
        make_record("m1", 1100, chat_id="1@c.us", chat_name="A", text="inside"),
        make_record("o1", 1200, chat_id="2@c.us", chat_name="B", text="other"),
    ]

    threads = group_records(records, selected, window)

    assert [thread.meta.name for thread in threads] == ["B", "A"]
    anna = next(thread for thread in threads if thread.meta.name == "A")
    assert anna.meta.hash == "aa"
    assert [message["msgId"] for message in anna.messages] == ["c1", "m1"]
    assert [message["inWindow"] for message in anna.messages] == [False, True]
    assert anna.last_ts == 1100


def test_group_records_drops_context_only_chat():
    window = make_window(1000, 2000)
    records = [make_record("c1", 900)]

    assert group_records(records, [], window) == []


def test_group_records_maps_dom_record_by_name():
    window = make_window(1000, 2000)
    selected = [make_entry("aa", "1@c.us", "Anna")]
    records = [make_record("m1", 1100, chat_id=None, chat_name="Anna")]

    threads = group_records(records, selected, window)

    assert len(threads) == 1
    assert threads[0].meta.hash == "aa"
    assert threads[0].meta.id == "1@c.us"
    assert [message["msgId"] for message in threads[0].messages] == ["m1"]


def test_group_records_does_not_merge_ambiguous_dom_names():
    window = make_window(1000, 2000)
    selected = [
        make_entry("aa", "1@c.us", "Anna"),
        make_entry("bb", "2@c.us", "Anna"),
    ]
    records = [make_record("m1", 1100, chat_id=None, chat_name="Anna")]

    threads = group_records(records, selected, window)

    assert len(threads) == 1
    assert threads[0].meta.hash is None
    assert threads[0].meta.id is None
    assert threads[0].meta.name == "Anna"


def test_group_records_sorts_by_latest_in_window_activity():
    window = make_window(1000, 2000)
    records = [
        make_record("a1", 1100, chat_id="a@c.us", chat_name="A"),
        make_record("b1", 1200, chat_id="b@c.us", chat_name="B"),
        make_record("a2", 1300, chat_id="a@c.us", chat_name="A"),
    ]

    threads = group_records(records, [], window)

    assert [thread.meta.name for thread in threads] == ["A", "B"]
    assert [thread.last_ts for thread in threads] == [1300, 1200]


def test_build_chunks_packs_without_splitting_messages():
    window = make_window(1000, 2000)
    selected = [make_entry("aa", "1@c.us", "A")]
    records = [
        make_record("m1", 1000, chat_id="1@c.us", text="aaaaa"),
        make_record("m2", 1100, chat_id="1@c.us", text="bbbbb"),
        make_record("m3", 1200, chat_id="1@c.us", text="ccccc"),
    ]
    threads = group_records(records, selected, window)

    chunks = build_chunks(threads, window, 0, 80, UTC)

    assert len(chunks) == 2
    assert [chunk.parts for chunk in chunks] == [2, 2]
    assert [chunk.part for chunk in chunks] == [1, 2]
    assert [chunk.oversized for chunk in chunks] == [False, False]
    assert [message["msgId"] for message in chunks[0].messages] == ["m1", "m2"]
    assert [message["msgId"] for message in chunks[1].messages] == ["m3"]
    assert chunks[0].chunk_id == "aa:1000:1100:1"
    assert chunks[1].chunk_id == "aa:1200:1200:2"


def test_build_chunks_isolates_oversized_message():
    window = make_window(1000, 2000)
    selected = [make_entry("aa", "1@c.us", "A")]
    records = [
        make_record("m1", 1000, chat_id="1@c.us", text="x" * 200),
        make_record("m2", 1100, chat_id="1@c.us", text="y"),
    ]
    threads = group_records(records, selected, window)

    chunks = build_chunks(threads, window, 0, 80, UTC)

    assert len(chunks) == 2
    assert chunks[0].oversized is True
    assert [message["msgId"] for message in chunks[0].messages] == ["m1"]
    assert chunks[1].oversized is False
    assert [message["msgId"] for message in chunks[1].messages] == ["m2"]


def test_build_chunks_without_cap_returns_one_part():
    window = make_window(1000, 2000)
    selected = [make_entry("aa", "1@c.us", "A")]
    records = [
        make_record("m1", 1000, chat_id="1@c.us", text="a" * 200),
        make_record("m2", 1100, chat_id="1@c.us", text="b" * 200),
    ]
    threads = group_records(records, selected, window)

    chunks = build_chunks(threads, window, 0, 0, UTC)

    assert len(chunks) == 1
    assert chunks[0].parts == 1
    assert chunks[0].oversized is False
    assert [message["msgId"] for message in chunks[0].messages] == ["m1", "m2"]


def test_render_chunks_text_has_headers_context_and_escaped_newlines():
    window = make_window(1000, 2000)
    selected = [make_entry("aa", "1@c.us", "A", phone="123")]
    records = [
        make_record("c1", 900, chat_id="1@c.us", chat_name="A", text="before\nline"),
        make_record("m2", 1100, chat_id="1@c.us", chat_name="A", text="hello\nworld"),
    ]
    threads = group_records(records, selected, window)
    chunks = build_chunks(threads, window, 60_000, 0, UTC)

    text = "\n".join(render_chunks_text(chunks, UTC))

    assert "=== CHAT aa | A | dm | 123 ===" in text
    assert "[window " in text
    assert "[context before 1m]" in text
    assert "[context] ← Anna: before\\nline" in text
    assert "← Anna: hello\\nworld" in text
    assert "before\nline" not in text
    assert "=== END CHAT (1/1) ===" in text


def test_render_chunks_text_escapes_chat_name_line_breaks():
    window = make_window(1000, 2000)
    name = "Evil\n=== CHAT fake | Fake | dm | - ==="
    selected = [make_entry("aa", "1@c.us", name)]
    records = [make_record("m1", 1100, chat_id="1@c.us", chat_name=name)]
    threads = group_records(records, selected, window)
    chunks = build_chunks(threads, window, 0, 0, UTC)

    text = "\n".join(render_chunks_text(chunks, UTC))

    assert not any(line.startswith("=== CHAT fake") for line in text.splitlines())
    assert "\n=== CHAT fake" not in text


def test_render_chunks_text_escapes_media_status_line_breaks():
    window = make_window(1000, 2000)
    selected = [make_entry("aa", "1@c.us", "A")]
    records = [
        make_record(
            "m1",
            1100,
            chat_id="1@c.us",
            chat_name="A",
            media={"status": "stored\n=== CHAT fake | Fake | dm | - ==="},
            mediaExport={"status": "failed"},
        )
    ]
    threads = group_records(records, selected, window)
    chunks = build_chunks(threads, window, 0, 0, UTC)

    text = "\n".join(render_chunks_text(chunks, UTC))

    assert not any(line.startswith("=== CHAT fake") for line in text.splitlines())
    assert "\n=== CHAT fake" not in text
    assert "media: stored\\n=== CHAT fake" in text


def test_render_chunks_ndjson_contains_metadata():
    window = make_window(1000, 2000)
    selected = [make_entry("aa", "1@c.us", "A")]
    records = [make_record("m1", 1100, chat_id="1@c.us", chat_name="A")]
    threads = group_records(records, selected, window)
    chunks = build_chunks(threads, window, 0, 0, UTC)

    payload = json.loads(render_chunks_ndjson(chunks)[0])

    assert payload["chunkVersion"] == 1
    assert payload["chunkId"] == "aa:1100:1100:1"
    assert payload["chat"]["hash"] == "aa"
    assert payload["window"]["start"] == 1000
    assert payload["window"]["end"] == 2000
    assert payload["contextBeforeMs"] == 0
    assert payload["part"] == 1
    assert payload["parts"] == 1
    assert payload["oversized"] is False
    assert payload["messages"][0]["msgId"] == "m1"
    assert payload["messages"][0]["inWindow"] is True


def test_render_chunks_text_group_sender():
    window = make_window(1000, 2000)
    selected = [make_entry("gg", "1@g.us", "Family", is_group=True)]
    records = [
        make_record("g1", 1100, chat_id="1@g.us", chat_name="Family", senderName="Mark", text="hi")
    ]
    threads = group_records(records, selected, window)
    chunks = build_chunks(threads, window, 0, 0, UTC)

    text = "\n".join(render_chunks_text(chunks, UTC))

    assert "=== CHAT gg | Family | group | - ===" in text
    assert "← Mark: hi" in text


def test_attach_media_exports_maps_by_msg_id():
    window = make_window(1000, 2000)
    selected = [make_entry("aa", "1@c.us", "A")]
    records = [
        make_record("m1", 1100, chat_id="1@c.us", chat_name="A"),
        make_record(
            "IMG-1",
            1200,
            chat_id="1@c.us",
            chat_name="A",
            type="image",
            text="photo",
            media={"status": "stored"},
        ),
    ]
    threads = group_records(records, selected, window)

    attach_media_exports(
        threads,
        [{"msgId": "IMG-1", "status": "exported", "file": "/tmp/media/a.jpg", "error": None}],
    )

    messages = {message["msgId"]: message for thread in threads for message in thread.messages}
    assert "mediaExport" not in messages["m1"]
    assert messages["IMG-1"]["mediaExport"] == {
        "status": "exported",
        "file": "/tmp/media/a.jpg",
        "error": None,
    }


@pytest.mark.parametrize(
    ("extra", "entry", "expected"),
    [
        (
            {"type": "image", "media": {"status": "stored"}},
            {"status": "exported", "file": "/tmp/a.jpg", "error": None},
            '(file: "/tmp/a.jpg")',
        ),
        (
            {"type": "image", "media": {"status": "not_downloaded"}},
            {"status": "skipped", "file": None, "error": "media status not_downloaded"},
            "(media: not_downloaded)",
        ),
        (
            {"type": "image", "media": {"status": "stored"}},
            {"status": "skipped", "file": None, "error": "media exceeds size limit"},
            "(media: not exported)",
        ),
        (
            {"type": "image", "media": {"status": "stored"}},
            {"status": "failed", "file": None, "error": "sha256 mismatch"},
            "(media: export failed)",
        ),
        (
            {"type": "image"},
            {"status": "skipped", "file": None, "error": "no media"},
            "(media: unavailable)",
        ),
        (
            {"type": "image", "media": {"status": "stored"}},
            {"status": "exported", "file": "/tmp/wa media/a.jpg", "error": None},
            '(file: "/tmp/wa media/a.jpg")',
        ),
    ],
)
def test_render_chunks_text_media_annotation(extra, entry, expected):
    window = make_window(1000, 2000)
    selected = [make_entry("aa", "1@c.us", "A")]
    records = [
        make_record(
            "IMG-1",
            1100,
            chat_id="1@c.us",
            chat_name="A",
            text="photo",
            **extra,
        )
    ]
    threads = group_records(records, selected, window)
    attach_media_exports(threads, [{"msgId": "IMG-1", **entry}])
    chunks = build_chunks(threads, window, 0, 0, UTC)

    text = "\n".join(render_chunks_text(chunks, UTC))

    assert expected in text
    assert "[image] photo" in text
