"""Range planning, decryption, exact filtering, dedupe and guards."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from conftest import GROUP_JID, KEY, LID_JID, make_cfg, ts_at

from whatsdrop_cli.chats import ChatEntry, load_directory, select_chats
from whatsdrop_cli.config import Window, resolve_window
from whatsdrop_cli.crypto import derive_keys
from whatsdrop_cli.dropbox import DropboxClient
from whatsdrop_cli.errors import GuardError, NoMatchesError
from whatsdrop_cli.fetch import collect, plan_tasks


def ctx(api, **overrides):
    base = api.endpoint
    cfg = make_cfg(
        api_base=f"{base}/2",
        content_base=f"{base}/2",
        token_url=f"{base}/oauth2/token",
        **overrides,
    )
    keys = derive_keys(KEY)
    client = DropboxClient(cfg)
    entries = load_directory(client, cfg, keys)
    return cfg, client, keys, entries


def test_plan_tasks_pads_dates(seeded):
    cfg, client, keys, entries = ctx(seeded)
    chats = select_chats(entries, ["*Anna*"], [], [], [], keys[0])
    window = resolve_window("2026-10-17", "2026-10-17", None, cfg.tz)

    tasks = plan_tasks(client, cfg, chats, window)
    keys_in_range = {task.key for task in tasks}
    assert len(keys_in_range) == 4
    assert any("/dt=2026-10-16/" in key for key in keys_in_range)
    assert any("/dt=2026-10-17/" in key for key in keys_in_range)


def test_collect_filters_exact_ts_and_dedupes(seeded):
    cfg, client, keys, entries = ctx(seeded)
    chats = select_chats(entries, ["*Anna*"], [], [], [], keys[0])
    window = resolve_window("2026-10-17", "2026-10-17", None, cfg.tz)

    records = collect(client, cfg, keys, plan_tasks(client, cfg, chats, window), window)
    assert [record["msgId"] for record in records] == ["A2"]
    assert records[0]["text"] == "repaired copy"
    assert records[0]["source"] == "store"
    assert records[0]["chatName"] == "Anna Schmidt"


def test_collect_includes_previous_day(seeded):
    cfg, client, keys, entries = ctx(seeded)
    chats = select_chats(entries, ["*Anna*"], [], [], [], keys[0])
    window = resolve_window("2026-10-16", "2026-10-17", None, cfg.tz)

    records = collect(client, cfg, keys, plan_tasks(client, cfg, chats, window), window)
    assert [record["msgId"] for record in records] == ["A1", "A2"]
    assert records[0]["text"] == "late message"


def test_group_pipeline(seeded):
    cfg, client, keys, entries = ctx(seeded)
    chats = select_chats(entries, ["Family*"], [], [], [], keys[0])
    assert chats and chats[0].chat_id == GROUP_JID
    window = resolve_window("2026-10-17", "2026-10-17", None, cfg.tz)

    records = collect(client, cfg, keys, plan_tasks(client, cfg, chats, window), window)
    assert [record["msgId"] for record in records] == ["G1"]
    assert records[0]["senderName"] == "Mark"


def test_lid_chat_via_phone(seeded):
    cfg, client, keys, entries = ctx(seeded)
    chats = select_chats(entries, [], ["49170000000"], [], [], keys[0])
    assert chats and chats[0].chat_id == LID_JID
    window = resolve_window("2026-10-17", "2026-10-17", None, cfg.tz)

    records = collect(client, cfg, keys, plan_tasks(client, cfg, chats, window), window)
    assert [record["msgId"] for record in records] == ["L1"]
    assert records[0]["chatName"] == "Bob LID"


def test_max_parts_guard(seeded):
    cfg, client, keys, entries = ctx(seeded)
    chats = select_chats(entries, ["*Anna*"], [], [], [], keys[0])
    window = resolve_window("2026-10-16", "2026-10-17", None, cfg.tz)
    with pytest.raises(GuardError):
        plan_tasks(client, ctx(seeded, max_parts=1)[0], chats, window)


def test_no_tasks_for_empty_window(seeded):
    cfg, client, keys, entries = ctx(seeded)
    chats = select_chats(entries, ["*Anna*"], [], [], [], keys[0])
    window = resolve_window("2020-01-01", "2020-01-02", None, cfg.tz)
    with pytest.raises(NoMatchesError):
        plan_tasks(client, cfg, chats, window)


def test_unknown_chat_folder_is_empty(seeded):
    cfg, client, keys, _ = ctx(seeded)
    window = resolve_window("2026-10-17", "2026-10-17", None, cfg.tz)
    with pytest.raises(NoMatchesError):
        plan_tasks(client, cfg, [], window)


def test_inactive_chat_is_skipped(seeded):
    cfg, client, keys, entries = ctx(seeded)
    anna = select_chats(entries, ["*Anna*"], [], [], [], keys[0])[0]
    window = resolve_window("2026-10-17", "2026-10-17", None, cfg.tz)

    old = replace(anna, last_seen=ts_at(10))
    with pytest.raises(NoMatchesError):
        plan_tasks(client, cfg, [old], window)

    future = replace(anna, first_seen=ts_at(20))
    with pytest.raises(NoMatchesError):
        plan_tasks(client, cfg, [future], window)


def test_part_selection_uses_intervals_and_legacy_anchor(seeded):
    cfg, client, keys, entries = ctx(seeded)
    chat_hash_value = "0123456789abcdef"
    base = f"/account=primary/chat={chat_hash_value}/dt=1970-01-01"
    for name in [
        "part-100-0.enc",  # legacy, entirely before the window
        "part-150-160-0.enc",  # new, before the window
        "part-200-1.enc",  # legacy anchor (last firstTs <= start)
        "part-240-260-1.enc",  # new, overlaps
        "part-300-2.enc",  # legacy, inside window
        "part-400-500-2.enc",  # new, overlaps
    ]:
        seeded.files[f"{base}/{name}"] = b"x"

    chat = ChatEntry(hash=chat_hash_value, chat_id="x@lid", chat_name="X", is_group=False)
    window = Window(
        start_ms=250,
        end_ms=450,
        start=datetime(1970, 1, 1, tzinfo=timezone.utc),
        end=datetime(1970, 1, 1, tzinfo=timezone.utc),
        label="test",
    )
    names = sorted(task.key.rsplit("/", 1)[-1] for task in plan_tasks(client, cfg, [chat], window))
    assert names == [
        "part-200-1.enc",
        "part-240-260-1.enc",
        "part-300-2.enc",
        "part-400-500-2.enc",
    ]
