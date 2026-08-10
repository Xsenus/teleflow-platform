from __future__ import annotations

import csv
import io
from datetime import datetime, time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_cipher, get_current_user, require_roles
from app.enums import (
    ConnectionStatus,
    DestinationValidationStatus,
    PermissionStatus,
    SafetySeverity,
    UserRole,
)
from app.models import (
    CampaignDestination,
    Destination,
    DestinationValidationRecord,
    TelegramConnection,
    User,
    utcnow,
)
from app.schemas import (
    DestinationBatchValidationItem,
    DestinationBatchValidationRequest,
    DestinationBatchValidationResponse,
    DestinationCreate,
    DestinationDiscoveryImportRequest,
    DestinationImportResponse,
    DestinationImportRow,
    DestinationPatch,
    DestinationRead,
    DestinationValidationRecordRead,
    DestinationValidationResponse,
    MessageResponse,
)
from app.security import aware_utc
from app.services.crypto import SecretCipher
from app.services.destination_bulk import (
    DestinationImportError,
    ParsedDestinationRow,
    parse_destination_file,
)
from app.services.destination_validation import (
    record_validation_failure,
    record_validation_success,
)
from app.services.telegram.errors import TelegramGatewayError
from app.services.telegram.factory import build_gateway

router = APIRouter(prefix="/destinations", tags=["destinations"])


def _get_destination(db: Session, destination_id: str, organization_id: str) -> Destination:
    """Реализовать внутренний этап get destination step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    destination = db.scalar(
        select(Destination).where(
            Destination.id == destination_id, Destination.organization_id == organization_id
        )
    )
    if not destination:
        raise HTTPException(status_code=404, detail="Назначение не найдено")
    return destination


def _gateway_error(exc: TelegramGatewayError) -> HTTPException:
    """Реализовать внутренний этап gateway error step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return HTTPException(
        status_code=503 if exc.transient else 400,
        detail={"code": exc.code, "message": str(exc), "retry_after": exc.retry_after},
    )


def _perform_destination_validation(
    *,
    destination: Destination,
    user: User,
    db: Session,
    settings: Settings,
    cipher: SecretCipher,
    source: str,
) -> tuple[DestinationValidationStatus, dict[str, object], TelegramGatewayError | None]:
    """Реализовать внутренний этап perform destination validation step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    try:
        resolved = build_gateway(destination.connection, settings, cipher).resolve_destination(
            chat_id=destination.telegram_chat_id,
            username=destination.username,
            topic_id=destination.topic_id,
        )
    except TelegramGatewayError as exc:
        record = record_validation_failure(
            db,
            destination=destination,
            error_code=exc.code,
            error_message=str(exc),
            checked_by=user,
            source=source,
        )
        return record.status, {}, exc
    record = record_validation_success(
        db,
        destination=destination,
        resolved=resolved,
        settings=settings,
        checked_by=user,
        source=source,
    )
    return record.status, dict(resolved.capabilities or {}), None


@router.get("", response_model=list[DestinationRead])
def list_destinations(
    connection_id: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Destination]:
    """Прочитать destinations. Значение возвращается без несвязанных изменений состояния."""
    stmt = (
        select(Destination)
        .where(Destination.organization_id == _user.organization_id)
        .order_by(Destination.title)
    )
    if connection_id:
        stmt = stmt.where(Destination.connection_id == connection_id)
    return list(db.scalars(stmt).all())


@router.post("/validate-batch", response_model=DestinationBatchValidationResponse)
def validate_destinations_batch(
    payload: DestinationBatchValidationRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> DestinationBatchValidationResponse:
    """Проверить destinations batch. Некорректные данные или состояние отклоняются до побочного
    эффекта.
    """
    items: list[DestinationBatchValidationItem] = []
    deferred_reason: str | None = None
    for destination_id in payload.destination_ids:
        if deferred_reason:
            items.append(
                DestinationBatchValidationItem(
                    destination_id=destination_id,
                    ok=False,
                    status="deferred",
                    title="Проверка отложена",
                    error_code="BATCH_DEFERRED",
                    error_message=deferred_reason,
                )
            )
            continue
        destination = db.scalar(
            select(Destination).where(
                Destination.id == destination_id,
                Destination.organization_id == user.organization_id,
            )
        )
        if destination is None:
            items.append(
                DestinationBatchValidationItem(
                    destination_id=destination_id,
                    ok=False,
                    status="failed",
                    title="Назначение недоступно",
                    error_code="DESTINATION_NOT_FOUND",
                    error_message="Назначение не найдено в текущей организации",
                )
            )
            continue
        status, capabilities, error = _perform_destination_validation(
            destination=destination,
            user=user,
            db=db,
            settings=settings,
            cipher=cipher,
            source="batch",
        )
        items.append(
            DestinationBatchValidationItem(
                destination_id=destination.id,
                ok=status == DestinationValidationStatus.PASSED,
                status=status.value,
                title=destination.title,
                capabilities=capabilities,
                error_code=error.code if error else destination.last_error_code,
                error_message=str(error) if error else destination.last_error_message,
            )
        )
        if error and error.retry_after:
            deferred_reason = (
                f"Telegram потребовал паузу {error.retry_after} сек.; "
                "оставшиеся назначения не проверялись"
            )

    summary = {
        "total": len(items),
        "passed": sum(item.status == "passed" for item in items),
        "failed": sum(item.status == "failed" for item in items),
        "write_forbidden": sum(item.status == "write_forbidden" for item in items),
        "deferred": sum(item.status == "deferred" for item in items),
    }
    write_audit(
        db,
        action="destinations.batch_validated",
        actor=user,
        entity_type="destination_validation_batch",
        details={**summary, "destination_ids": payload.destination_ids},
        request=request,
    )
    db.commit()
    return DestinationBatchValidationResponse(items=items, **summary)


def _bulk_destination_result(
    *,
    rows: list[ParsedDestinationRow],
    connection: TelegramConnection,
    user: User,
    request: Request,
    db: Session,
    settings: Settings,
    cipher: SecretCipher,
    dry_run: bool,
) -> DestinationImportResponse:
    """Реализовать внутренний этап bulk destination result step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    gateway = build_gateway(connection, settings, cipher)
    results: list[DestinationImportRow] = []
    seen: set[tuple[int, int | None]] = set()
    deferred_reason: str | None = None
    created_count = 0

    for parsed in rows:
        if deferred_reason:
            results.append(
                DestinationImportRow(
                    row_number=parsed.row_number,
                    source=parsed.source,
                    status="deferred",
                    message=deferred_reason,
                    username=parsed.username,
                    telegram_chat_id=parsed.telegram_chat_id,
                    topic_id=parsed.topic_id,
                    title=parsed.title,
                    permission_confirmed=parsed.permission_confirmed,
                )
            )
            continue
        if parsed.error:
            results.append(
                DestinationImportRow(
                    row_number=parsed.row_number,
                    source=parsed.source,
                    status="error",
                    message=parsed.error,
                    username=parsed.username,
                    telegram_chat_id=parsed.telegram_chat_id,
                    topic_id=parsed.topic_id,
                    title=parsed.title,
                    permission_confirmed=parsed.permission_confirmed,
                )
            )
            continue
        try:
            payload = DestinationCreate.model_validate(parsed.payload(connection.id))
        except ValidationError as exc:
            message = (
                exc.errors()[0].get("msg", "Некорректная строка") if exc.errors() else str(exc)
            )
            results.append(
                DestinationImportRow(
                    row_number=parsed.row_number,
                    source=parsed.source,
                    status="error",
                    message=str(message),
                    username=parsed.username,
                    telegram_chat_id=parsed.telegram_chat_id,
                    topic_id=parsed.topic_id,
                    title=parsed.title,
                    permission_confirmed=parsed.permission_confirmed,
                )
            )
            continue
        try:
            resolved = gateway.resolve_destination(
                chat_id=payload.telegram_chat_id,
                username=payload.username,
                topic_id=payload.topic_id,
            )
        except TelegramGatewayError as exc:
            results.append(
                DestinationImportRow(
                    row_number=parsed.row_number,
                    source=parsed.source,
                    status="error",
                    message=f"{exc.code}: {str(exc)}",
                    username=parsed.username,
                    telegram_chat_id=parsed.telegram_chat_id,
                    topic_id=parsed.topic_id,
                    title=parsed.title,
                    permission_confirmed=parsed.permission_confirmed,
                )
            )
            if exc.retry_after:
                deferred_reason = f"Импорт остановлен по требованию Telegram; повторите после {exc.retry_after} сек."
            continue

        key = (resolved.chat_id, payload.topic_id)
        duplicate = key in seen or db.scalar(
            select(Destination.id).where(
                Destination.connection_id == connection.id,
                Destination.telegram_chat_id == resolved.chat_id,
                Destination.topic_id.is_(None)
                if payload.topic_id is None
                else Destination.topic_id == payload.topic_id,
            )
        )
        if duplicate:
            results.append(
                DestinationImportRow(
                    row_number=parsed.row_number,
                    source=parsed.source,
                    status="duplicate",
                    message="Назначение уже существует или повторяется в файле",
                    username=resolved.username,
                    telegram_chat_id=resolved.chat_id,
                    topic_id=payload.topic_id,
                    title=payload.title or resolved.title,
                    permission_confirmed=payload.permission_confirmed,
                )
            )
            continue
        seen.add(key)

        destination: Destination | None = None
        if not dry_run:
            destination = Destination(
                organization_id=user.organization_id,
                connection_id=connection.id,
                telegram_chat_id=resolved.chat_id,
                username=resolved.username,
                title=payload.title or resolved.title,
                kind=resolved.kind,
                topic_id=payload.topic_id,
                enabled=parsed.enabled,
                validated=False,
                permission_status=(
                    PermissionStatus.CONFIRMED
                    if payload.permission_confirmed
                    else PermissionStatus.UNVERIFIED
                ),
                permission_note=payload.permission_note,
                rules_url=str(payload.rules_url) if payload.rules_url else None,
                permission_confirmed_at=utcnow() if payload.permission_confirmed else None,
                permission_confirmed_by_id=user.id if payload.permission_confirmed else None,
                permission_reviewed_at=utcnow() if payload.permission_confirmed else None,
                permission_expires_at=payload.permission_expires_at,
                timezone_name=payload.timezone_name,
                allowed_weekdays=payload.allowed_weekdays,
                allowed_start_time=payload.allowed_start_time,
                allowed_end_time=payload.allowed_end_time,
                cooldown_minutes_override=payload.cooldown_minutes_override,
            )
            try:
                with db.begin_nested():
                    db.add(destination)
                    db.flush()
                    record_validation_success(
                        db,
                        destination=destination,
                        resolved=resolved,
                        settings=settings,
                        checked_by=user,
                        source="bulk_import",
                        display_title=destination.title,
                    )
            except IntegrityError:
                results.append(
                    DestinationImportRow(
                        row_number=parsed.row_number,
                        source=parsed.source,
                        status="duplicate",
                        message="Назначение было добавлено другим процессом во время импорта",
                        username=resolved.username,
                        telegram_chat_id=resolved.chat_id,
                        topic_id=payload.topic_id,
                        title=payload.title or resolved.title,
                        permission_confirmed=payload.permission_confirmed,
                    )
                )
                continue
            created_count += 1

        results.append(
            DestinationImportRow(
                row_number=parsed.row_number,
                source=parsed.source,
                status="ready" if dry_run else "created",
                message=(
                    "Готово к импорту; разрешение нужно подтвердить отдельно"
                    if dry_run and not payload.permission_confirmed
                    else "Готово к импорту"
                    if dry_run
                    else "Назначение создано"
                ),
                username=resolved.username,
                telegram_chat_id=resolved.chat_id,
                topic_id=payload.topic_id,
                title=payload.title or resolved.title,
                permission_confirmed=payload.permission_confirmed,
                destination_id=destination.id if destination else None,
            )
        )

    if not dry_run:
        write_audit(
            db,
            action="destinations.bulk_imported",
            actor=user,
            entity_type="telegram_connection",
            entity_id=connection.id,
            details={
                "total": len(rows),
                "created": created_count,
                "duplicates": sum(item.status == "duplicate" for item in results),
                "errors": sum(item.status == "error" for item in results),
                "deferred": sum(item.status == "deferred" for item in results),
            },
            request=request,
        )
        db.commit()

    return DestinationImportResponse(
        dry_run=dry_run,
        total=len(results),
        ready=sum(item.status == "ready" for item in results),
        created=sum(item.status == "created" for item in results),
        duplicates=sum(item.status == "duplicate" for item in results),
        errors=sum(item.status == "error" for item in results),
        deferred=sum(item.status == "deferred" for item in results),
        rows=results,
    )


async def _read_bulk_file(
    file: UploadFile,
    settings: Settings,
) -> list[ParsedDestinationRow]:
    """Реализовать внутренний этап read bulk file step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    max_bytes = min(settings.max_media_bytes, 2 * 1024 * 1024)
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Файл импорта не должен превышать 2 МБ")
    try:
        return parse_destination_file(content, file.filename, max_rows=500)
    except DestinationImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _rows_from_discovery(payload: DestinationDiscoveryImportRequest) -> list[ParsedDestinationRow]:
    """Реализовать внутренний этап rows from discovery step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    rules_url = str(payload.rules_url) if payload.rules_url else None
    return [
        ParsedDestinationRow(
            row_number=index,
            source=item.username or str(item.telegram_chat_id),
            username=item.username,
            telegram_chat_id=item.telegram_chat_id,
            topic_id=item.topic_id,
            title=item.title,
            kind=item.kind,
            permission_confirmed=payload.permission_confirmed,
            permission_note=payload.permission_note,
            rules_url=rules_url,
            permission_expires_at=payload.permission_expires_at,
            timezone_name=payload.timezone_name,
            allowed_weekdays=payload.allowed_weekdays,
            allowed_start_time=payload.allowed_start_time,
            allowed_end_time=payload.allowed_end_time,
            cooldown_minutes_override=payload.cooldown_minutes_override,
        )
        for index, item in enumerate(payload.items, 1)
    ]


@router.post("/bulk/preview", response_model=DestinationImportResponse)
async def preview_destination_import(
    request: Request,
    connection_id: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> DestinationImportResponse:
    """Выполнить операцию preview destination import. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    connection = db.scalar(
        select(TelegramConnection).where(
            TelegramConnection.id == connection_id,
            TelegramConnection.organization_id == user.organization_id,
        )
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Telegram-подключение не найдено")
    if connection.status != ConnectionStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Подключение должно быть активно")
    rows = await _read_bulk_file(file, settings)
    return _bulk_destination_result(
        rows=rows,
        connection=connection,
        user=user,
        request=request,
        db=db,
        settings=settings,
        cipher=cipher,
        dry_run=True,
    )


@router.post("/bulk/apply", response_model=DestinationImportResponse)
async def apply_destination_import(
    request: Request,
    connection_id: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> DestinationImportResponse:
    """Выполнить операцию apply destination import. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    connection = db.scalar(
        select(TelegramConnection).where(
            TelegramConnection.id == connection_id,
            TelegramConnection.organization_id == user.organization_id,
        )
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Telegram-подключение не найдено")
    if connection.status != ConnectionStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Подключение должно быть активно")
    rows = await _read_bulk_file(file, settings)
    return _bulk_destination_result(
        rows=rows,
        connection=connection,
        user=user,
        request=request,
        db=db,
        settings=settings,
        cipher=cipher,
        dry_run=False,
    )


@router.post("/bulk/discovery", response_model=DestinationImportResponse)
def import_discovered_destinations(
    payload: DestinationDiscoveryImportRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> DestinationImportResponse:
    """Создать discovered destinations. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    connection = db.scalar(
        select(TelegramConnection).where(
            TelegramConnection.id == payload.connection_id,
            TelegramConnection.organization_id == user.organization_id,
        )
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Telegram-подключение не найдено")
    if connection.status != ConnectionStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Подключение должно быть активно")
    return _bulk_destination_result(
        rows=_rows_from_discovery(payload),
        connection=connection,
        user=user,
        request=request,
        db=db,
        settings=settings,
        cipher=cipher,
        dry_run=False,
    )


@router.get("/export.csv")
def export_destinations_csv(
    connection_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Выполнить операцию export destinations csv. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    stmt = select(Destination).where(Destination.organization_id == user.organization_id)
    if connection_id:
        stmt = stmt.where(Destination.connection_id == connection_id)
    items = list(db.scalars(stmt.order_by(Destination.title)).all())
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "link",
            "username",
            "telegram_chat_id",
            "topic_id",
            "title",
            "kind",
            "permission_confirmed",
            "permission_note",
            "rules_url",
            "enabled",
            "timezone_name",
            "allowed_weekdays",
            "allowed_start_time",
            "allowed_end_time",
            "cooldown_minutes_override",
        ]
    )
    for item in items:
        writer.writerow(
            [
                f"https://t.me/{item.username}" if item.username else "",
                item.username or "",
                item.telegram_chat_id or "",
                item.topic_id or "",
                item.title,
                item.kind.value,
                item.permission_status == PermissionStatus.CONFIRMED,
                item.permission_note or "",
                item.rules_url or "",
                item.enabled,
                item.timezone_name or "",
                "|".join(str(day) for day in (item.allowed_weekdays or [])),
                item.allowed_start_time.strftime("%H:%M") if item.allowed_start_time else "",
                item.allowed_end_time.strftime("%H:%M") if item.allowed_end_time else "",
                item.cooldown_minutes_override or "",
            ]
        )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="teleflow-destinations.csv"'},
    )


@router.post("", response_model=DestinationRead, status_code=201)
def create_destination(
    payload: DestinationCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> Destination:
    """Создать destination. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    connection = db.scalar(
        select(TelegramConnection).where(
            TelegramConnection.id == payload.connection_id,
            TelegramConnection.organization_id == user.organization_id,
        )
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Telegram-подключение не найдено")
    if connection.status != ConnectionStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Подключение должно быть активно")
    try:
        resolved = build_gateway(connection, settings, cipher).resolve_destination(
            chat_id=payload.telegram_chat_id,
            username=payload.username,
            topic_id=payload.topic_id,
        )
    except TelegramGatewayError as exc:
        raise _gateway_error(exc) from exc

    duplicate = db.scalar(
        select(Destination).where(
            Destination.connection_id == connection.id,
            Destination.telegram_chat_id == resolved.chat_id,
            Destination.topic_id.is_(None)
            if payload.topic_id is None
            else Destination.topic_id == payload.topic_id,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Такое назначение уже добавлено")

    permission_status = (
        PermissionStatus.CONFIRMED if payload.permission_confirmed else PermissionStatus.UNVERIFIED
    )
    destination = Destination(
        organization_id=user.organization_id,
        connection_id=connection.id,
        telegram_chat_id=resolved.chat_id,
        username=resolved.username,
        title=payload.title or resolved.title,
        kind=resolved.kind,
        topic_id=payload.topic_id,
        enabled=True,
        validated=False,
        permission_status=permission_status,
        permission_note=payload.permission_note,
        rules_url=str(payload.rules_url) if payload.rules_url else None,
        permission_confirmed_at=utcnow() if payload.permission_confirmed else None,
        permission_confirmed_by_id=user.id if payload.permission_confirmed else None,
        permission_reviewed_at=utcnow() if payload.permission_confirmed else None,
        permission_expires_at=payload.permission_expires_at,
        timezone_name=payload.timezone_name,
        allowed_weekdays=payload.allowed_weekdays,
        allowed_start_time=payload.allowed_start_time,
        allowed_end_time=payload.allowed_end_time,
        cooldown_minutes_override=payload.cooldown_minutes_override,
    )
    db.add(destination)
    db.flush()
    record_validation_success(
        db,
        destination=destination,
        resolved=resolved,
        settings=settings,
        checked_by=user,
        source="create",
        display_title=destination.title,
    )
    write_audit(
        db,
        action="destination.created",
        actor=user,
        entity_type="destination",
        entity_id=destination.id,
        details={
            "connection_id": connection.id,
            "chat_id": resolved.chat_id,
            "topic_id": payload.topic_id,
            "permission_status": permission_status.value,
            "capabilities": resolved.capabilities,
        },
        request=request,
    )
    db.commit()
    db.refresh(destination)
    return destination


@router.get("/{destination_id}", response_model=DestinationRead)
def get_destination(
    destination_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Destination:
    """Прочитать destination. Значение возвращается без несвязанных изменений состояния."""
    return _get_destination(db, destination_id, _user.organization_id)


@router.patch("/{destination_id}", response_model=DestinationRead)
def patch_destination(
    destination_id: str,
    payload: DestinationPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> Destination:
    """Обновить destination. Переход применяется только после проверки его предусловий."""
    destination = _get_destination(db, destination_id, user.organization_id)
    data = payload.model_dump(exclude_unset=True)
    if "rules_url" in data and data["rules_url"] is not None:
        data["rules_url"] = str(data["rules_url"])
    if "allowed_weekdays" in data and data["allowed_weekdays"] is None:
        data["allowed_weekdays"] = []
    next_start = data.get("allowed_start_time", destination.allowed_start_time)
    next_end = data.get("allowed_end_time", destination.allowed_end_time)
    if (next_start is None) != (next_end is None):
        raise HTTPException(
            status_code=422,
            detail="Начало и окончание временного окна задаются вместе",
        )
    if next_start is not None and next_start == next_end:
        raise HTTPException(
            status_code=422,
            detail="Начало и окончание временного окна не должны совпадать",
        )
    next_status = data.get("permission_status", destination.permission_status)
    note = data.get("permission_note", destination.permission_note)
    rules_url = data.get("rules_url", destination.rules_url)
    if next_status == PermissionStatus.CONFIRMED and not (note or rules_url):
        raise HTTPException(
            status_code=422,
            detail="Для подтверждения разрешения требуется примечание или ссылка на правила",
        )
    next_expiry = data.get("permission_expires_at", destination.permission_expires_at)
    normalized_expiry = aware_utc(next_expiry)
    if (
        next_status == PermissionStatus.CONFIRMED
        and normalized_expiry is not None
        and normalized_expiry <= utcnow()
    ):
        raise HTTPException(status_code=422, detail="Срок разрешения должен быть в будущем")
    if next_status == PermissionStatus.CONFIRMED:
        reviewed_at = utcnow()
        destination.permission_confirmed_at = reviewed_at
        destination.permission_confirmed_by_id = user.id
        destination.permission_reviewed_at = reviewed_at
    elif next_status in {PermissionStatus.DENIED, PermissionStatus.UNVERIFIED}:
        if next_status == PermissionStatus.DENIED:
            data["enabled"] = False
        destination.permission_confirmed_at = None
        destination.permission_confirmed_by_id = None
        destination.permission_reviewed_at = None
        data["permission_expires_at"] = None
    for key, value in data.items():
        setattr(destination, key, value)
    audit_details = {
        key: value.isoformat()
        if isinstance(value, datetime)
        else value.isoformat(timespec="minutes")
        if isinstance(value, time)
        else value
        for key, value in data.items()
    }
    write_audit(
        db,
        action="destination.updated",
        actor=user,
        entity_type="destination",
        entity_id=destination.id,
        details=audit_details,
        request=request,
    )
    db.commit()
    db.refresh(destination)
    return destination


@router.post("/{destination_id}/validate", response_model=DestinationValidationResponse)
def validate_destination(
    destination_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> DestinationValidationResponse:
    """Проверить destination. Некорректные данные или состояние отклоняются до побочного эффекта."""
    destination = _get_destination(db, destination_id, user.organization_id)
    status, capabilities, error = _perform_destination_validation(
        destination=destination,
        user=user,
        db=db,
        settings=settings,
        cipher=cipher,
        source="manual",
    )
    if error:
        write_audit(
            db,
            action="destination.validation_failed",
            actor=user,
            entity_type="destination",
            entity_id=destination.id,
            severity=SafetySeverity.WARNING,
            details={"code": error.code},
            request=request,
        )
        db.commit()
        raise _gateway_error(error) from error
    write_audit(
        db,
        action="destination.validated",
        actor=user,
        entity_type="destination",
        entity_id=destination.id,
        details={"capabilities": capabilities, "status": status.value},
        request=request,
    )
    db.commit()
    db.refresh(destination)
    return DestinationValidationResponse(
        ok=status == DestinationValidationStatus.PASSED and destination.enabled,
        destination=DestinationRead.model_validate(destination),
        capabilities=capabilities,
    )


@router.get(
    "/{destination_id}/validation-history",
    response_model=list[DestinationValidationRecordRead],
)
def destination_validation_history(
    destination_id: str,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DestinationValidationRecord]:
    """Выполнить операцию destination validation history. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    _get_destination(db, destination_id, user.organization_id)
    limit = min(max(limit, 1), 200)
    return list(
        db.scalars(
            select(DestinationValidationRecord)
            .where(
                DestinationValidationRecord.organization_id == user.organization_id,
                DestinationValidationRecord.destination_id == destination_id,
            )
            .order_by(DestinationValidationRecord.checked_at.desc())
            .limit(limit)
        ).all()
    )


@router.delete("/{destination_id}", response_model=MessageResponse)
def delete_destination(
    destination_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete destination. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    destination = _get_destination(db, destination_id, user.organization_id)
    linked = (
        db.scalar(
            select(func.count(CampaignDestination.id)).where(
                CampaignDestination.destination_id == destination.id,
                CampaignDestination.organization_id == user.organization_id,
            )
        )
        or 0
    )
    if linked:
        raise HTTPException(
            status_code=409,
            detail="Назначение используется в кампаниях. Отключите его вместо удаления",
        )
    write_audit(
        db,
        action="destination.deleted",
        actor=user,
        entity_type="destination",
        entity_id=destination.id,
        request=request,
    )
    db.delete(destination)
    db.commit()
    return MessageResponse(message="Назначение удалено")
