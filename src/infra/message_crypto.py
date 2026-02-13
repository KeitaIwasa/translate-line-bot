from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _load_key(secret: str) -> bytes:
    """秘密鍵文字列を32byte鍵へ正規化する。"""
    raw = (secret or "").strip()
    if not raw:
        raise ValueError("MESSAGE_ENCRYPTION_KEY is empty")

    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(raw + ("=" * (-len(raw) % 4)))
            if len(decoded) in {16, 24, 32}:
                return decoded
        except Exception:  # pylint: disable=broad-except
            pass

    # base64 以外は SHA-256 で32byte化して利用する
    return hashlib.sha256(raw.encode("utf-8")).digest()



def encrypt_text(plain_text: str, *, key_secret: str, aad: Optional[bytes] = None) -> str:
    key = _load_key(key_secret)
    aes = AESGCM(key)
    nonce = os.urandom(12)
    cipher = aes.encrypt(nonce, (plain_text or "").encode("utf-8"), aad)
    blob = nonce + cipher
    return base64.urlsafe_b64encode(blob).decode("ascii")



def decrypt_text(cipher_text: str, *, key_secret: str, aad: Optional[bytes] = None) -> str:
    key = _load_key(key_secret)
    raw = base64.urlsafe_b64decode(cipher_text.encode("ascii"))
    if len(raw) < 13:
        raise ValueError("invalid encrypted payload")
    nonce, cipher = raw[:12], raw[12:]
    plain = AESGCM(key).decrypt(nonce, cipher, aad)
    return plain.decode("utf-8")
