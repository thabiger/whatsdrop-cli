"""Crypto contract tests (shared vector with the extension's JS tests)."""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from whatsdrop_cli.crypto import (
    chat_hash,
    decrypt_media,
    decrypt_text,
    derive_keys,
    derive_media_key,
    encrypt_media,
    is_valid_key,
    media_hash,
)

KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
ENVELOPE = bytes.fromhex(
    "5741415201000102030405060708090a0b099a064dea34234a0aa4ba909557229ff4eb0aaf668f288d1fd48193ef"
)
MEDIA_PATH = "chat=88bbb4101bc5c104/dt=2026-10-17/media-00112233445566778899aabbccddeeff.enc"
MEDIA_ENVELOPE = bytes.fromhex(
    "57414d5201000102030405060708090a0bc9514062a056768c55a6317cd8d5a3946948749784fe0b4944edb4"
)


def test_known_answer_vector():
    hash_key, enc_key = derive_keys(KEY)
    assert decrypt_text(enc_key, ENVELOPE) == "hello archive"
    assert chat_hash(hash_key, "49171234567@c.us") == "88bbb4101bc5c104"


def test_media_known_answer_vector():
    hash_key, _ = derive_keys(KEY)
    assert (
        media_hash(hash_key, "ACE81BE4695181CE094591658F7D7844")
        == "5b18a54d4201b67cdf305c22c0a5f9b5"
    )
    media_key = derive_media_key(KEY)
    assert decrypt_media(media_key, MEDIA_PATH, MEDIA_ENVELOPE) == b"hello media"


def test_media_roundtrip_and_path_binding():
    media_key = derive_media_key(KEY)
    encrypted = encrypt_media(media_key, MEDIA_PATH, b"hello media")
    assert decrypt_media(media_key, MEDIA_PATH, encrypted) == b"hello media"
    with pytest.raises(InvalidTag):
        decrypt_media(media_key, MEDIA_PATH + ".x", encrypted)


def test_crypto_roundtrip():
    from whatsdrop_cli.crypto import encrypt_text

    _, enc_key = derive_keys(KEY)
    encrypted = encrypt_text(enc_key, "some payload")
    assert decrypt_text(enc_key, encrypted) == "some payload"


def test_tampering_fails():
    _, enc_key = derive_keys(KEY)
    tampered = bytearray(ENVELOPE)
    tampered[-1] ^= 0xFF
    with pytest.raises(InvalidTag):
        decrypt_text(enc_key, bytes(tampered))


def test_wrong_key_fails():
    _, other_key = derive_keys("BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc=")
    with pytest.raises(InvalidTag):
        decrypt_text(other_key, ENVELOPE)


def test_key_validation():
    assert is_valid_key(KEY)
    assert not is_valid_key("abc")
    assert not is_valid_key("")
    with pytest.raises(ValueError):
        derive_keys("abc")
