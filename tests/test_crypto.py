import pytest

from personal_ai_os.core.crypto import decrypt, encrypt


def test_encrypt_roundtrip() -> None:
    key = "00" * 32
    msg = "токен-доступа"
    c = encrypt(msg, key)
    assert decrypt(c, key) == msg
