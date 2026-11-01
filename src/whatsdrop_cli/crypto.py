"""Client-side archive crypto (must match the extension's background/crypto.js).

Contract:
  K       = 32-byte master key (base64)
  hashKey = HKDF-SHA256(ikm=K, salt=b"wa-archive-v1", info=b"chat-hash", 32)
  encKey  = HKDF-SHA256(ikm=K, salt=b"wa-archive-v1", info=b"object-encryption", 32)
  mediaKey = HKDF-SHA256(ikm=K, salt=b"wa-archive-v1", info=b"media-encryption", 32)
  chatHash = lowerhex(HMAC-SHA256(hashKey, chatId))[:16]
  mediaHash = lowerhex(HMAC-SHA256(hashKey, b"media:" + msgId))[:32]
  object   = b"WAAR" | 0x01 | nonce(12) | AES-256-GCM ciphertext+tag (AAD = first 5 bytes)
  media    = b"WAMR" | 0x01 | nonce(12) | AES-256-GCM ciphertext+tag (AAD = header + path)

Plaintext mode uses unkeyed SHA-256 path hashes:
  chatHash  = lowerhex(SHA-256(chatId))[:16]
  mediaHash = lowerhex(SHA-256(b"media:" + msgId))[:32]
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"WAAR"
VERSION = 1
MEDIA_MAGIC = b"WAMR"
MEDIA_VERSION = 1
SALT = b"wa-archive-v1"


def decode_key(key_base64: str) -> bytes:
    try:
        raw = base64.b64decode(str(key_base64 or ""), validate=True)
    except Exception as exc:
        raise ValueError("encryption key is not valid base64") from exc
    if len(raw) != 32:
        raise ValueError("encryption key must be 32 bytes (base64)")
    return raw


def is_valid_key(key_base64: str) -> bool:
    try:
        decode_key(key_base64)
        return True
    except ValueError:
        return False


def derive_keys(key_base64: str) -> tuple[bytes, bytes]:
    master = decode_key(key_base64)
    hash_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=SALT, info=b"chat-hash").derive(
        master
    )
    enc_key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=SALT, info=b"object-encryption"
    ).derive(master)
    return hash_key, enc_key


def derive_media_key(key_base64: str) -> bytes:
    master = decode_key(key_base64)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=SALT, info=b"media-encryption").derive(
        master
    )


def chat_hash(hash_key: bytes, chat_id: str) -> str:
    return hmac.new(hash_key, str(chat_id).encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def media_hash(hash_key: bytes, msg_id: str) -> str:
    return hmac.new(hash_key, f"media:{msg_id}".encode(), hashlib.sha256).hexdigest()[:32]


def plaintext_chat_hash(chat_id: str) -> str:
    return hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:16]


def plaintext_media_hash(msg_id: str) -> str:
    return hashlib.sha256(f"media:{msg_id}".encode()).hexdigest()[:32]


def decrypt_text(enc_key: bytes, envelope: bytes) -> str:
    data = bytes(envelope)
    header = len(MAGIC) + 1
    if len(data) <= header + 12 + 16:
        raise ValueError("encrypted object is too short")
    if data[: len(MAGIC)] != MAGIC:
        raise ValueError("bad envelope magic")
    if data[len(MAGIC)] != VERSION:
        raise ValueError("unsupported envelope version")
    nonce = data[header : header + 12]
    ciphertext = data[header + 12 :]
    plaintext = AESGCM(enc_key).decrypt(nonce, ciphertext, data[:header])
    return plaintext.decode("utf-8")


def encrypt_text(enc_key: bytes, text: str) -> bytes:
    """Encrypt helper (tests/backup tooling); production only decrypts."""
    nonce = os.urandom(12)
    header = MAGIC + bytes([VERSION])
    ciphertext = AESGCM(enc_key).encrypt(nonce, text.encode("utf-8"), header)
    return header + nonce + ciphertext


def _media_aad(relative_path: str) -> bytes:
    return MEDIA_MAGIC + bytes([MEDIA_VERSION]) + str(relative_path).encode("utf-8")


def decrypt_media(media_key: bytes, relative_path: str, envelope: bytes) -> bytes:
    data = bytes(envelope)
    header = len(MEDIA_MAGIC) + 1
    if len(data) <= header + 12 + 16:
        raise ValueError("media envelope is too short")
    if data[: len(MEDIA_MAGIC)] != MEDIA_MAGIC:
        raise ValueError("bad media envelope magic")
    if data[len(MEDIA_MAGIC)] != MEDIA_VERSION:
        raise ValueError("unsupported media envelope version")
    nonce = data[header : header + 12]
    ciphertext = data[header + 12 :]
    return AESGCM(media_key).decrypt(nonce, ciphertext, _media_aad(relative_path))


def encrypt_media(
    media_key: bytes, relative_path: str, payload: bytes, nonce: bytes | None = None
) -> bytes:
    """Encrypt helper (tests/backup tooling); production only decrypts."""
    nonce = nonce or os.urandom(12)
    if len(nonce) != 12:
        raise ValueError("nonce must be 12 bytes")
    header = MEDIA_MAGIC + bytes([MEDIA_VERSION])
    ciphertext = AESGCM(media_key).encrypt(nonce, bytes(payload), _media_aad(relative_path))
    return header + nonce + ciphertext
