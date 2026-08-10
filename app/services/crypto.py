from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretCipher:
    """AES-256-GCM envelope encryption for Telegram and 2FA secrets."""

    PREFIX = "v1."

    def __init__(self, master_key: str):
        """Инициализировать SecretCipher with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self._key = self._derive_key(master_key)
        self._aes = AESGCM(self._key)

    @staticmethod
    def _derive_key(master_key: str) -> bytes:
        """Реализовать внутренний этап derive key step класса SecretCipher. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        if master_key.startswith("base64:"):
            raw = base64.urlsafe_b64decode(master_key.removeprefix("base64:").encode("ascii"))
            if len(raw) != 32:
                raise ValueError("Base64 master key должен содержать ровно 32 байта")
            return raw
        return hashlib.sha256(master_key.encode("utf-8")).digest()

    @staticmethod
    def generate_master_key() -> str:
        """Выполнить операцию generate master key класса SecretCipher. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        return "base64:" + base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")

    def encrypt(self, value: str, *, context: str) -> str:
        """Выполнить операцию encrypt класса SecretCipher. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        nonce = os.urandom(12)
        encrypted = self._aes.encrypt(nonce, value.encode("utf-8"), context.encode("utf-8"))
        payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
        return self.PREFIX + payload

    def decrypt(self, value: str, *, context: str) -> str:
        """Выполнить операцию decrypt класса SecretCipher. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        if not value.startswith(self.PREFIX):
            raise ValueError("Неизвестная версия зашифрованного секрета")
        payload = base64.urlsafe_b64decode(value.removeprefix(self.PREFIX).encode("ascii"))
        if len(payload) < 13:
            raise ValueError("Повреждённый зашифрованный секрет")
        nonce, encrypted = payload[:12], payload[12:]
        plain = self._aes.decrypt(nonce, encrypted, context.encode("utf-8"))
        return plain.decode("utf-8")

    def encrypt_json(self, value: dict[str, Any], *, context: str) -> str:
        """Выполнить операцию encrypt json класса SecretCipher. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return self.encrypt(raw, context=context)

    def decrypt_json(self, value: str, *, context: str) -> dict[str, Any]:
        """Выполнить операцию decrypt json класса SecretCipher. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        return json.loads(self.decrypt(value, context=context))
