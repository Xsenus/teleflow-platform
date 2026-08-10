from __future__ import annotations

import codecs
import hashlib
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_current_user, require_roles
from app.enums import SafetySeverity, UserRole
from app.models import DeliveryJob, MediaAsset, MessageTemplate, User
from app.schemas import MediaAssetRead, MessageResponse
from app.services.antivirus import AntivirusUnavailable, MalwareDetected, scan_media_bytes
from app.services.storage import StorageService

router = APIRouter(prefix="/media", tags=["media"])

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "video/mp4",
    "application/pdf",
    "application/zip",
    "text/plain",
}


def _safe_extension(filename: str) -> str:
    """Реализовать внутренний этап safe extension step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    suffix = Path(filename).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ""


def _signature_matches(content_type: str, header: bytes) -> bool:
    """Проверить the declared type against a conservative file signature."""
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


def _read_validated_upload(file: UploadFile, content_type: str, limit: int) -> tuple[bytes, str]:
    """Реализовать внутренний этап read validated upload step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    digest = hashlib.sha256()
    size = 0
    header = b""
    chunks: list[bytes] = []
    text_decoder = (
        codecs.getincrementaldecoder("utf-8")(errors="strict")
        if content_type == "text/plain"
        else None
    )
    while chunk := file.file.read(1024 * 1024):
        if len(header) < 4096:
            header += chunk[: 4096 - len(header)]
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail="Файл превышает допустимый размер")
        if text_decoder:
            try:
                text_decoder.decode(chunk, final=False)
            except UnicodeDecodeError as exc:
                raise HTTPException(
                    status_code=415, detail="Текстовый файл должен быть корректным UTF-8"
                ) from exc
        digest.update(chunk)
        chunks.append(chunk)
    if text_decoder:
        try:
            text_decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=415, detail="Текстовый файл должен быть корректным UTF-8"
            ) from exc
    if size == 0:
        raise HTTPException(status_code=400, detail="Пустой файл не поддерживается")
    if not _signature_matches(content_type, header):
        raise HTTPException(
            status_code=415, detail="Содержимое файла не соответствует заявленному типу"
        )
    return b"".join(chunks), digest.hexdigest()


@router.get("", response_model=list[MediaAssetRead])
def list_media(
    _user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[MediaAsset]:
    """Прочитать media. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(MediaAsset)
            .where(MediaAsset.organization_id == _user.organization_id)
            .order_by(MediaAsset.created_at.desc())
        ).all()
    )


@router.post("", response_model=MediaAssetRead, status_code=201)
def upload_media(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MediaAsset:
    """Выполнить операцию upload media. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    content_type = (file.content_type or "application/octet-stream").lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Тип файла не разрешён")
    data, sha256 = _read_validated_upload(file, content_type, settings.max_media_bytes)
    scan_status = "disabled"
    try:
        scan_result = scan_media_bytes(data, settings)
        scan_status = scan_result.status
    except MalwareDetected as exc:
        write_audit(
            db,
            action="media.upload_blocked_malware",
            actor=user,
            entity_type="media_upload",
            severity=SafetySeverity.CRITICAL,
            details={
                "filename": (file.filename or "unnamed")[:255],
                "content_type": content_type,
                "size_bytes": len(data),
                "sha256": sha256,
                "signature": exc.signature,
            },
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=422, detail="Файл заблокирован антивирусной проверкой"
        ) from exc
    except AntivirusUnavailable as exc:
        write_audit(
            db,
            action="media.antivirus_unavailable",
            actor=user,
            entity_type="media_upload",
            severity=SafetySeverity.WARNING,
            details={
                "filename": (file.filename or "unnamed")[:255],
                "content_type": content_type,
                "size_bytes": len(data),
                "sha256": sha256,
                "fail_closed": settings.antivirus_fail_closed,
                "error": str(exc),
            },
            request=request,
        )
        db.commit()
        if settings.antivirus_fail_closed:
            raise HTTPException(
                status_code=503,
                detail="Антивирусная проверка временно недоступна; файл не сохранён",
            ) from exc
        scan_status = "unavailable_fail_open"
    stored_name = f"{uuid.uuid4().hex}{_safe_extension(file.filename or '')}"
    key = f"media/{user.organization_id}/{stored_name}"
    storage: StorageService = request.app.state.storage
    storage.put_bytes(key, data, content_type=content_type)
    asset = MediaAsset(
        organization_id=user.organization_id,
        original_name=(file.filename or stored_name)[:255],
        stored_name=stored_name,
        relative_path=key,
        storage_backend=settings.storage_backend,
        storage_key=key,
        content_type=content_type,
        size_bytes=len(data),
        sha256=sha256,
        created_by_id=user.id,
    )
    try:
        db.add(asset)
        db.flush()
        write_audit(
            db,
            action="media.uploaded",
            actor=user,
            entity_type="media_asset",
            entity_id=asset.id,
            details={
                "content_type": content_type,
                "size_bytes": len(data),
                "sha256": asset.sha256,
                "storage_backend": settings.storage_backend,
                "antivirus_status": scan_status,
                "antivirus_mode": settings.antivirus_mode,
            },
            request=request,
        )
        db.commit()
        db.refresh(asset)
    except Exception:
        db.rollback()
        storage.delete(key)
        raise
    return asset


@router.delete("/{asset_id}", response_model=MessageResponse)
def delete_media(
    asset_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete media. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    asset = db.scalar(
        select(MediaAsset).where(
            MediaAsset.id == asset_id, MediaAsset.organization_id == user.organization_id
        )
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Файл не найден")
    templates = (
        db.scalar(
            select(func.count(MessageTemplate.id)).where(
                MessageTemplate.media_asset_id == asset.id,
                MessageTemplate.organization_id == user.organization_id,
            )
        )
        or 0
    )
    jobs = (
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.media_asset_id == asset.id,
                DeliveryJob.organization_id == user.organization_id,
            )
        )
        or 0
    )
    if templates or jobs:
        raise HTTPException(status_code=409, detail="Файл используется шаблоном или заданием")
    key = asset.storage_key or asset.relative_path
    write_audit(
        db,
        action="media.deleted",
        actor=user,
        entity_type="media_asset",
        entity_id=asset.id,
        request=request,
    )
    db.delete(asset)
    db.commit()
    request.app.state.storage.delete(key)
    return MessageResponse(message="Файл удалён")
