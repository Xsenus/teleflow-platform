from __future__ import annotations

import codecs
import hashlib
from typing import BinaryIO

ALLOWED_MEDIA_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "video/mp4",
    "application/pdf",
    "application/zip",
    "text/plain",
}


class MediaValidationError(ValueError):
    pass


def signature_matches(content_type: str, header: bytes) -> bool:
    """Выполнить операцию signature matches. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    content_type = content_type.lower()
    if content_type == "image/jpeg":
        return header.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if content_type == "image/gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "video/mp4":
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if content_type == "application/pdf":
        return header.startswith(b"%PDF-")
    if content_type == "application/zip":
        return header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
    if content_type == "text/plain":
        return b"\x00" not in header
    return False


def validate_media_bytes(data: bytes, content_type: str, limit: int) -> str:
    """Проверить media bytes. Некорректные данные или состояние отклоняются до побочного эффекта."""
    content_type = content_type.lower()
    if content_type not in ALLOWED_MEDIA_CONTENT_TYPES:
        raise MediaValidationError("Тип файла не разрешён")
    if not data:
        raise MediaValidationError("Пустой файл не поддерживается")
    if len(data) > limit:
        raise MediaValidationError("Файл превышает допустимый размер")
    header = data[:4096]
    if not signature_matches(content_type, header):
        raise MediaValidationError("Содержимое файла не соответствует заявленному типу")
    if content_type == "text/plain":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MediaValidationError("Текстовый файл должен быть корректным UTF-8") from exc
    return hashlib.sha256(data).hexdigest()


def read_validated_stream(stream: BinaryIO, content_type: str, limit: int) -> tuple[bytes, str]:
    """Прочитать validated stream. Значение возвращается без несвязанных изменений состояния."""
    content_type = content_type.lower()
    if content_type not in ALLOWED_MEDIA_CONTENT_TYPES:
        raise MediaValidationError("Тип файла не разрешён")
    digest = hashlib.sha256()
    size = 0
    header = b""
    chunks: list[bytes] = []
    text_decoder = (
        codecs.getincrementaldecoder("utf-8")(errors="strict")
        if content_type == "text/plain"
        else None
    )
    while chunk := stream.read(1024 * 1024):
        if len(header) < 4096:
            header += chunk[: 4096 - len(header)]
        size += len(chunk)
        if size > limit:
            raise MediaValidationError("Файл превышает допустимый размер")
        if text_decoder:
            try:
                text_decoder.decode(chunk, final=False)
            except UnicodeDecodeError as exc:
                raise MediaValidationError("Текстовый файл должен быть корректным UTF-8") from exc
        digest.update(chunk)
        chunks.append(chunk)
    if text_decoder:
        try:
            text_decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise MediaValidationError("Текстовый файл должен быть корректным UTF-8") from exc
    if size == 0:
        raise MediaValidationError("Пустой файл не поддерживается")
    if not signature_matches(content_type, header):
        raise MediaValidationError("Содержимое файла не соответствует заявленному типу")
    return b"".join(chunks), digest.hexdigest()
