from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import SafetySeverity
from app.models import AuditChainState, AuditLog, User, new_id, utcnow
from app.security import aware_utc, extract_client_ip

SENSITIVE_KEYS = {
    "password",
    "token",
    "bot_token",
    "api_hash",
    "session",
    "code",
    "totp",
    "secret",
    "credentials",
    "authorization",
    "phone",
    "email",
    "body_enc",
}
ZERO_HASH = "0" * 64
SYSTEM_CHAIN_KEY = "__system__"
CHAIN_VERSION = 1


def redact(value: Any) -> Any:
    """Выполнить операцию redact. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if any(secret in lowered for secret in SENSITIVE_KEYS):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    return value


def _chain_key(organization_id: str | None) -> str:
    """Реализовать внутренний этап chain key step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return organization_id or SYSTEM_CHAIN_KEY


def _iso(value: datetime | None) -> str | None:
    """Реализовать внутренний этап iso step. Вспомогательная функция сохраняет детерминированность
    и тестируемость процесса.
    """
    if value is None:
        return None
    normalized = aware_utc(value) or value.replace(tzinfo=UTC)
    return normalized.isoformat(timespec="microseconds")


def _canonical_entry_payload(
    *,
    log_id: str,
    organization_id: str | None,
    actor_user_id: str | None,
    action: str,
    entity_type: str | None,
    entity_id: str | None,
    severity: SafetySeverity | str,
    ip_address: str | None,
    user_agent: str | None,
    request_id: str | None,
    details: dict[str, Any],
    created_at: datetime,
    sequence: int,
    prev_hash: str,
    chain_version: int = CHAIN_VERSION,
) -> bytes:
    """Реализовать внутренний этап canonical entry payload step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    severity_value = severity.value if isinstance(severity, SafetySeverity) else str(severity)
    payload = {
        "id": log_id,
        "organization_id": organization_id,
        "actor_user_id": actor_user_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "severity": severity_value,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "request_id": request_id,
        "details": details,
        "created_at": _iso(created_at),
        "sequence": sequence,
        "prev_hash": prev_hash,
        "chain_version": chain_version,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _entry_hash(prev_hash: str, payload: bytes) -> str:
    """Вычислить entry hash. Канонический ввод обеспечивает детерминированное сравнение целостности
    между процессами.
    """
    digest = hashlib.sha256()
    digest.update(prev_hash.encode("ascii"))
    digest.update(b"\n")
    digest.update(payload)
    return digest.hexdigest()


def _lock_chain_state(db: Session, *, organization_id: str | None) -> AuditChainState:
    """Реализовать внутренний этап lock chain state step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    chain_key = _chain_key(organization_id)
    state = db.scalar(
        select(AuditChainState).where(AuditChainState.chain_key == chain_key).with_for_update()
    )
    if state is None:
        state = AuditChainState(
            chain_key=chain_key,
            organization_id=organization_id,
            last_sequence=0,
            last_hash=ZERO_HASH,
            updated_at=utcnow(),
        )
        db.add(state)
        db.flush()
    return state


def write_audit(
    db: Session,
    *,
    action: str,
    actor: User | None = None,
    organization_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    severity: SafetySeverity = SafetySeverity.INFO,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AuditLog:
    """Выполнить операцию write audit. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    resolved_org_id = organization_id or (actor.organization_id if actor else None)
    if resolved_org_id is None and request is not None:
        resolved_org_id = getattr(request.state, "organization_id", None)

    redacted_details = redact(details or {})
    ip_address = extract_client_ip(request) if request else None
    user_agent = (request.headers.get("user-agent", "")[:500] or None) if request else None
    request_id = getattr(request.state, "request_id", None) if request else None
    created_at = utcnow()
    log_id = new_id()

    state = _lock_chain_state(db, organization_id=resolved_org_id)
    sequence = int(state.last_sequence or 0) + 1
    prev_hash = state.last_hash or ZERO_HASH
    payload = _canonical_entry_payload(
        log_id=log_id,
        organization_id=resolved_org_id,
        actor_user_id=actor.id if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        severity=severity,
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=request_id,
        details=redacted_details,
        created_at=created_at,
        sequence=sequence,
        prev_hash=prev_hash,
    )
    entry_hash = _entry_hash(prev_hash, payload)

    log = AuditLog(
        id=log_id,
        organization_id=resolved_org_id,
        actor_user_id=actor.id if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        severity=severity,
        details=redacted_details,
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=request_id,
        sequence=sequence,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        chain_version=CHAIN_VERSION,
        created_at=created_at,
    )
    db.add(log)
    state.last_sequence = sequence
    state.last_hash = entry_hash
    state.updated_at = created_at
    return log


@dataclass(frozen=True)
class AuditVerificationResult:
    valid: bool
    organization_id: str | None
    checked_entries: int
    legacy_entries: int
    first_error: str | None
    first_error_entry_id: str | None
    head_sequence: int
    computed_head_hash: str
    stored_head_hash: str | None
    verified_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """Преобразовать to dict класса AuditVerificationResult without changing the source object."""
        payload = asdict(self)
        payload["verified_at"] = self.verified_at.isoformat()
        return payload


def _hash_existing_log(log: AuditLog) -> str:
    """Вычислить hash existing log. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    if log.sequence is None or log.prev_hash is None or log.chain_version is None:
        raise ValueError("Запись не включена в hash-chain")
    payload = _canonical_entry_payload(
        log_id=log.id,
        organization_id=log.organization_id,
        actor_user_id=log.actor_user_id,
        action=log.action,
        entity_type=log.entity_type,
        entity_id=log.entity_id,
        severity=log.severity,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
        request_id=log.request_id,
        details=log.details or {},
        created_at=log.created_at,
        sequence=int(log.sequence),
        prev_hash=log.prev_hash,
        chain_version=int(log.chain_version),
    )
    return _entry_hash(log.prev_hash, payload)


def verify_audit_chain(db: Session, *, organization_id: str | None) -> AuditVerificationResult:
    """Проверить audit chain. Некорректные данные или состояние отклоняются до побочного эффекта."""
    logs = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.organization_id == organization_id)
            .order_by(AuditLog.created_at, AuditLog.id)
        ).all()
    )
    legacy = [item for item in logs if item.sequence is None or item.entry_hash is None]
    chained = [item for item in logs if item.sequence is not None and item.entry_hash is not None]
    chained.sort(key=lambda item: (int(item.sequence or 0), item.created_at, item.id))

    expected_sequence = 1
    expected_prev = ZERO_HASH
    first_error: str | None = None
    first_error_entry_id: str | None = None
    computed_head = ZERO_HASH

    for item in chained:
        if item.sequence != expected_sequence:
            first_error = (
                f"Нарушена последовательность: ожидалось {expected_sequence}, "
                f"получено {item.sequence}"
            )
            first_error_entry_id = item.id
            break
        if item.prev_hash != expected_prev:
            first_error = "Предыдущий hash не совпадает с вычисленной цепочкой"
            first_error_entry_id = item.id
            break
        calculated = _hash_existing_log(item)
        if item.entry_hash != calculated:
            first_error = "Hash записи не соответствует её содержимому"
            first_error_entry_id = item.id
            break
        computed_head = calculated
        expected_prev = calculated
        expected_sequence += 1

    state = db.get(AuditChainState, _chain_key(organization_id))
    if first_error is None:
        if state is None:
            first_error = "Отсутствует состояние hash-chain"
        elif state.last_sequence != len(chained):
            first_error = "Состояние цепочки содержит неверный номер последней записи"
        elif state.last_hash != computed_head:
            first_error = "Состояние цепочки содержит неверный head hash"

    return AuditVerificationResult(
        valid=first_error is None and not legacy,
        organization_id=organization_id,
        checked_entries=len(chained),
        legacy_entries=len(legacy),
        first_error=first_error,
        first_error_entry_id=first_error_entry_id,
        head_sequence=len(chained),
        computed_head_hash=computed_head,
        stored_head_hash=state.last_hash if state else None,
        verified_at=utcnow(),
    )


def backfill_audit_chain(db: Session, *, organization_id: str | None) -> AuditVerificationResult:
    """Построить a chain for legacy rows. This function must be run while API/worker writes are
    stopped. It rewrites only chain metadata and never changes the original audit event payload.
    """

    logs = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.organization_id == organization_id)
            .order_by(AuditLog.created_at, AuditLog.id)
            .with_for_update()
        ).all()
    )
    state = _lock_chain_state(db, organization_id=organization_id)
    state.last_sequence = 0
    state.last_hash = ZERO_HASH
    expected_prev = ZERO_HASH

    for sequence, item in enumerate(logs, start=1):
        item.sequence = sequence
        item.prev_hash = expected_prev
        item.chain_version = CHAIN_VERSION
        calculated = _hash_existing_log(item)
        item.entry_hash = calculated
        expected_prev = calculated

    state.last_sequence = len(logs)
    state.last_hash = expected_prev
    state.updated_at = utcnow()
    db.flush()
    return verify_audit_chain(db, organization_id=organization_id)
