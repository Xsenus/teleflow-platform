from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import boto3
import pytest

from app.config import Settings
from app.services.storage import StorageService


class FakeS3:
    """Имитировать минимальный S3 object store и записывать запросы."""

    def __init__(self) -> None:
        """Создать пустое хранилище и доступный bucket."""

        self.objects: dict[tuple[str, str], bytes] = {}
        self.bucket_available = True
        self.last_put: dict[str, Any] | None = None

    def put_object(self, **kwargs: Any) -> None:
        """Сохранить объект и параметры server-side encryption."""

        self.last_put = kwargs
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = bytes(kwargs["Body"])

    def get_object(self, **kwargs: Any) -> dict[str, io.BytesIO]:
        """Вернуть поток содержимого сохранённого объекта."""

        return {"Body": io.BytesIO(self.objects[(kwargs["Bucket"], kwargs["Key"])])}

    def delete_object(self, **kwargs: Any) -> None:
        """Удалить объект из тестового bucket."""

        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)

    def head_object(self, **kwargs: Any) -> dict[str, int]:
        """Подтвердить наличие объекта либо имитировать S3 404."""

        key = (kwargs["Bucket"], kwargs["Key"])
        if key not in self.objects:
            raise KeyError(key)
        return {"ContentLength": len(self.objects[key])}

    def head_bucket(self, **_kwargs: Any) -> dict[str, bool]:
        """Подтвердить доступность bucket либо выбросить сетевую ошибку."""

        if not self.bucket_available:
            raise ConnectionError("bucket unavailable")
        return {"ok": True}


def s3_settings(settings: Settings) -> Settings:
    """Создать валидные тестовые настройки S3 backend."""

    return settings.model_copy(
        update={
            "storage_backend": "s3",
            "s3_bucket": "teleflow-test",
            "s3_endpoint_url": "https://s3.example.test",
            "s3_region": "test-1",
            "s3_access_key": "access-key",
            "s3_secret_key": "secret-key",
            "s3_secure": True,
        }
    )


def test_s3_storage_roundtrip_health_and_materialize(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Проверить S3 put/read/exists/delete/healthcheck и временную materialization."""

    fake = FakeS3()
    captured: dict[str, Any] = {}

    def client(_service: str, **kwargs: Any) -> FakeS3:
        """Сохранить параметры создания boto3 client и вернуть fake S3."""

        captured.update(kwargs)
        return fake

    monkeypatch.setattr(boto3, "client", client)
    storage = StorageService(s3_settings(settings))

    key = storage.put_bytes("exports/report.bin", b"payload", content_type="application/test")
    assert key == "exports/report.bin"
    assert captured["endpoint_url"] == "https://s3.example.test"
    assert fake.last_put is not None
    assert fake.last_put["ServerSideEncryption"] == "AES256"
    assert fake.last_put["ContentType"] == "application/test"
    assert storage.read_bytes(key) == b"payload"
    assert storage.exists(key) is True
    assert storage.exists("missing.bin") is False
    assert storage.healthcheck() is True

    materialized: Path
    with storage.materialize(key, suffix=".bin") as path:
        materialized = path
        assert path.read_bytes() == b"payload"
        assert path.suffix == ".bin"
    assert materialized.exists() is False

    storage.delete(key)
    assert storage.exists(key) is False
    fake.bucket_available = False
    assert storage.healthcheck() is False
