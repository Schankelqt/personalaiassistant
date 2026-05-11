from __future__ import annotations

import os
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_LEN: Final = 12


def encrypt(plaintext: str, key_hex: str) -> str:
    key = bytes.fromhex(key_hex)
    aesgcm = AESGCM(key)
    nonce = os.urandom(NONCE_LEN)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return (nonce + ciphertext).hex()


def decrypt(ciphertext_hex: str, key_hex: str) -> str:
    key = bytes.fromhex(key_hex)
    data = bytes.fromhex(ciphertext_hex)
    nonce, ciphertext = data[:NONCE_LEN], data[NONCE_LEN:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode()
