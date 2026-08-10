from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from app.config import Settings


class StorageError(RuntimeError):
    pass


def _safe_key(key: str) -> str:
    """Реализовать внутренний этап safe key step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    normalized = str(PurePosixPath(key.lstrip("/")))
    if normalized in {"", "."} or normalized.startswith("../") or "/../" in normalized:
        raise StorageError("Некорректный storage key")
    return normalized


class StorageService:
    def __init__(self, settings: Settings):
        """Инициализировать StorageService with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.settings = settings
        self.backend = settings.storage_backend
        self.root = settings.storage_path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._s3 = None
        if self.backend == "s3":
            try:
                import boto3
            except ImportError as exc:
                raise StorageError("Для S3 установите boto3") from exc
            self._s3 = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                region_name=settings.s3_region,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                use_ssl=settings.s3_secure,
            )

    def put_bytes(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> str:
        """Выполнить операцию put bytes класса StorageService. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        key = _safe_key(key)
        if self.backend == "local":
            path = self._local_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)
        else:
            assert self._s3 is not None and self.settings.s3_bucket
            self._s3.put_object(
                Bucket=self.settings.s3_bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
        return key

    def read_bytes(self, key: str) -> bytes:
        """Прочитать bytes класса StorageService. Значение возвращается без несвязанных изменений
        состояния.
        """
        key = _safe_key(key)
        if self.backend == "local":
            path = self._local_path(key)
            if not path.exists():
                raise FileNotFoundError(key)
            return path.read_bytes()
        assert self._s3 is not None and self.settings.s3_bucket
        response = self._s3.get_object(Bucket=self.settings.s3_bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        """Выполнить операцию delete класса StorageService. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        key = _safe_key(key)
        if self.backend == "local":
            self._local_path(key).unlink(missing_ok=True)
        else:
            assert self._s3 is not None and self.settings.s3_bucket
            self._s3.delete_object(Bucket=self.settings.s3_bucket, Key=key)

    def exists(self, key: str) -> bool:
        """Выполнить операцию exists класса StorageService. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        key = _safe_key(key)
        if self.backend == "local":
            return self._local_path(key).exists()
        assert self._s3 is not None and self.settings.s3_bucket
        try:
            self._s3.head_object(Bucket=self.settings.s3_bucket, Key=key)
            return True
        except Exception:
            return False

    def healthcheck(self) -> bool:
        """Выполнить операцию healthcheck класса StorageService. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        try:
            if self.backend == "local":
                self.root.mkdir(parents=True, exist_ok=True)
                return self.root.exists() and os.access(self.root, os.R_OK | os.W_OK)
            assert self._s3 is not None and self.settings.s3_bucket
            self._s3.head_bucket(Bucket=self.settings.s3_bucket)
            return True
        except Exception:
            return False

    @contextlib.contextmanager
    def materialize(self, key: str, *, suffix: str = "") -> Iterator[Path]:
        """Выполнить операцию materialize класса StorageService. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        key = _safe_key(key)
        if self.backend == "local":
            yield self._local_path(key)
            return
        data = self.read_bytes(key)
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            handle.write(data)
            handle.close()
            yield Path(handle.name)
        finally:
            Path(handle.name).unlink(missing_ok=True)

    def _local_path(self, key: str) -> Path:
        """Реализовать внутренний этап local path step класса StorageService. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        path = (self.root / _safe_key(key)).resolve()
        if self.root != path and self.root not in path.parents:
            raise StorageError("Storage path выходит за пределы корня")
        return path
