"""Chat-directory loading and selector matching."""

from __future__ import annotations

from conftest import A_JID, GROUP_JID, KEY, LID_JID, make_cfg

from whatsdrop_cli.archive import ArchiveCodec
from whatsdrop_cli.chats import ChatEntry, load_directory, select_chats
from whatsdrop_cli.crypto import plaintext_chat_hash
from whatsdrop_cli.dropbox import DropboxClient


def ctx(api, mode="encrypted", key=KEY):
    base = api.endpoint
    cfg = make_cfg(
        archive_key=key if mode == "encrypted" else "",
        api_base=f"{base}/2",
        content_base=f"{base}/2",
        token_url=f"{base}/oauth2/token",
    )
    codec = ArchiveCodec(mode, cfg.archive_key)
    return cfg, DropboxClient(cfg), codec


def test_load_directory_from_index(seeded):
    cfg, client, codec = ctx(seeded)
    entries = load_directory(client, cfg, codec)

    assert len(entries) == 3
    assert {entry.chat_name for entry in entries} == {"Anna Schmidt", "Bob LID", "Family Group"}
    anna = next(entry for entry in entries if entry.chat_id == A_JID)
    assert anna.hash == codec.chat_hash(A_JID)
    assert anna.chat_phone == "49171234567"
    assert anna.chat_pushname == "Anna"
    assert anna.is_group is False


def test_load_directory_fallback_scan(seeded):
    cfg, client, codec = ctx(seeded)
    del seeded.files["/account=primary/_chats/index.enc"]

    entries = load_directory(client, cfg, codec)
    assert len(entries) == 3
    assert any(entry.chat_name == "Anna Schmidt" for entry in entries)


def test_load_plaintext_directory_from_index(seeded_plaintext):
    cfg, client, codec = ctx(seeded_plaintext, mode="plaintext")
    entries = load_directory(client, cfg, codec)

    assert len(entries) == 3
    anna = next(entry for entry in entries if entry.chat_id == A_JID)
    assert anna.hash == plaintext_chat_hash(A_JID)
    assert anna.chat_name == "Anna Schmidt"


def test_load_plaintext_directory_fallback_scan(seeded_plaintext):
    cfg, client, codec = ctx(seeded_plaintext, mode="plaintext")
    del seeded_plaintext.files["/account=primary/_chats/index.json"]

    entries = load_directory(client, cfg, codec)
    assert len(entries) == 3
    assert any(entry.chat_name == "Anna Schmidt" for entry in entries)


def test_select_by_name_and_pushname(seeded):
    cfg, client, codec = ctx(seeded)
    entries = load_directory(client, cfg, codec)

    assert [e.chat_name for e in select_chats(entries, ["*anna*"], [], [], [], codec)] == [
        "Anna Schmidt"
    ]
    assert select_chats(entries, ["family"], [], [], [], codec)[0].chat_id == GROUP_JID
    assert select_chats(entries, ["Bobby"], [], [], [], codec)[0].chat_id == LID_JID

    selected = select_chats(entries, ["Bob*", "Family*"], [], [], [], codec)
    assert {entry.chat_id for entry in selected} == {LID_JID, GROUP_JID}


def test_select_by_number_including_lid(seeded):
    cfg, client, codec = ctx(seeded)
    entries = load_directory(client, cfg, codec)

    assert select_chats(entries, [], ["+49 171 234567"], [], [], codec)[0].chat_id == A_JID
    assert select_chats(entries, [], ["49170000000"], [], [], codec)[0].chat_id == LID_JID


def test_select_by_hash_and_jid(seeded):
    cfg, client, codec = ctx(seeded)
    entries = load_directory(client, cfg, codec)

    group_hash = codec.chat_hash(GROUP_JID)
    assert select_chats(entries, [], [], [group_hash], [], codec)[0].chat_id == GROUP_JID
    assert select_chats(entries, [], [], [], [A_JID], codec)[0].chat_name == "Anna Schmidt"


def test_unknown_jid_synthesizes_entry(seeded):
    cfg, client, codec = ctx(seeded)
    entries = load_directory(client, cfg, codec)

    unknown = "49111111111@c.us"
    selected = select_chats(entries, [], [], [], [unknown], codec)
    assert len(selected) == 1
    assert selected[0].hash == codec.chat_hash(unknown)
    assert selected[0].chat_name == "unknown"


def test_plaintext_unknown_jid_uses_sha256(seeded_plaintext):
    cfg, client, codec = ctx(seeded_plaintext, mode="plaintext")
    entries = load_directory(client, cfg, codec)

    unknown = "49111111111@c.us"
    selected = select_chats(entries, [], [], [], [unknown], codec)

    assert selected[0].hash == plaintext_chat_hash(unknown)


def test_no_match_returns_empty(seeded):
    cfg, client, codec = ctx(seeded)
    entries = load_directory(client, cfg, codec)
    assert select_chats(entries, ["zzz*"], [], [], [], codec) == []


def test_select_keeps_distinct_entries_without_hash(seeded):
    cfg, client, codec = ctx(seeded)
    entries = [
        ChatEntry(hash="", chat_id="1@c.us", chat_name="Alpha"),
        ChatEntry(hash="", chat_id="2@c.us", chat_name="Alpha"),
    ]

    selected = select_chats(entries, ["Alpha"], [], [], [], codec)

    assert [entry.chat_id for entry in selected] == ["1@c.us", "2@c.us"]


def test_select_normalizes_formatted_chat_phone(seeded):
    cfg, client, codec = ctx(seeded)
    entries = [
        ChatEntry(
            hash="aa",
            chat_id="1@c.us",
            chat_name="Alpha",
            chat_phone="+49 171 234567",
        )
    ]

    selected = select_chats(entries, [], ["+49 171 234567"], [], [], codec)

    assert [entry.chat_id for entry in selected] == ["1@c.us"]
