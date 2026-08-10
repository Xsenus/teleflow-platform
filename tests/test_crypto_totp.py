from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag

from app.services.crypto import SecretCipher
from app.services.totp import code_at, generate_secret, verify_code


def test_secret_cipher_roundtrip_and_context_separation() -> None:
    """Проверить сценарий secret cipher roundtrip and context separation. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    cipher = SecretCipher("unit-test-master-key")
    encrypted = cipher.encrypt("sensitive", context="connection:1")
    assert encrypted.startswith("v1.")
    assert "sensitive" not in encrypted
    assert cipher.decrypt(encrypted, context="connection:1") == "sensitive"
    with pytest.raises(InvalidTag):
        cipher.decrypt(encrypted, context="connection:2")


def test_secret_cipher_detects_tampering() -> None:
    """Проверить сценарий secret cipher detects tampering. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    cipher = SecretCipher("unit-test-master-key")
    encrypted = cipher.encrypt("secret", context="ctx")
    raw = bytearray(base64.urlsafe_b64decode(encrypted.removeprefix("v1.")))
    raw[-1] ^= 1
    tampered = "v1." + base64.urlsafe_b64encode(raw).decode("ascii")
    with pytest.raises(InvalidTag):
        cipher.decrypt(tampered, context="ctx")


def test_totp_validates_code_at() -> None:
    """Проверить сценарий totp validates code at. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    secret = generate_secret()
    code = code_at(secret)
    assert verify_code(secret, code)
    assert not verify_code(secret, "000000") or code == "000000"
