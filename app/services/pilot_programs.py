from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import verify_audit_chain
from app.config import Settings
from app.enums import (
    JobStatus,
    PilotProgramStatus,
    PilotStageStatus,
    ReadinessStatus,
    RunStatus,
)
from app.models import (
    Campaign,
    CampaignDestination,
    CampaignRun,
    CommissioningCheckRun,
    DeliveryJob,
    Organization,
    PilotProgram,
    PilotStageExecution,
    User,
    utcnow,
)
from app.security import aware_utc
from app.services.approvals import approval_is_current, campaign_fingerprint
from app.services.artifact_signing import sign_bytes
from app.services.crypto import SecretCipher
from app.services.readiness import latest_readiness_report, readiness_is_current


class PilotProgramError(RuntimeError):
    pass


TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.PARTIAL,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


def load_program(
    db: Session,
    *,
    program_id: str,
    organization_id: str,
    for_update: bool = False,
) -> PilotProgram | None:
    """Прочитать program. Значение возвращается без несвязанных изменений состояния."""
    stmt = (
        select(PilotProgram)
        .where(
            PilotProgram.id == program_id,
            PilotProgram.organization_id == organization_id,
        )
        .options(
            selectinload(PilotProgram.stages),
            selectinload(PilotProgram.campaign)
            .selectinload(Campaign.destinations)
            .selectinload(CampaignDestination.destination),
        )
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def create_program(
    db: Session,
    *,
    organization_id: str,
    campaign: Campaign,
    name: str,
    stage_sizes: list[int],
    require_distinct_signoff: bool,
    notes: str | None,
    created_by: User,
) -> PilotProgram:
    """Создать program. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    existing = db.scalar(
        select(PilotProgram).where(
            PilotProgram.organization_id == organization_id,
            PilotProgram.campaign_id == campaign.id,
            PilotProgram.status.in_(
                [PilotProgramStatus.DRAFT, PilotProgramStatus.ACTIVE, PilotProgramStatus.PAUSED]
            ),
        )
    )
    if existing:
        raise PilotProgramError("Для кампании уже существует незавершённая программа пилота")

    program = PilotProgram(
        organization_id=organization_id,
        campaign_id=campaign.id,
        name=name,
        status=PilotProgramStatus.DRAFT,
        stage_sizes=stage_sizes,
        current_stage_order=0,
        require_distinct_signoff=require_distinct_signoff,
        notes=notes,
        created_by_id=created_by.id,
    )
    db.add(program)
    db.flush()

    stage_specs = [
        (0, "local_fake", "Локальная проверка в fake mode", 1, False),
        *[
            (
                index,
                "service_chat" if size == 1 else f"authorized_{size}",
                "Одна служебная группа" if size == 1 else f"Пакет из {size} разрешённых групп",
                size,
                True,
            )
            for index, size in enumerate(stage_sizes, start=1)
        ],
    ]
    for order, code, title, target, live in stage_specs:
        db.add(
            PilotStageExecution(
                organization_id=organization_id,
                program_id=program.id,
                stage_order=order,
                code=code,
                title=title,
                target_destination_count=target,
                requires_live_telegram=live,
                status=PilotStageStatus.PENDING,
            )
        )
    db.flush()
    db.refresh(program)
    return load_program(db, program_id=program.id, organization_id=organization_id) or program


def _latest_commissioning(db: Session, *, organization_id: str) -> CommissioningCheckRun | None:
    """Реализовать внутренний этап latest commissioning step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return db.scalar(
        select(CommissioningCheckRun)
        .where(CommissioningCheckRun.organization_id == organization_id)
        .order_by(CommissioningCheckRun.created_at.desc())
        .limit(1)
    )


def refresh_stage_state(
    db: Session,
    *,
    program: PilotProgram,
    stage: PilotStageExecution,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Выполнить операцию refresh stage state. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    now = aware_utc(now) or utcnow()
    campaign = program.campaign
    active_destination_count = sum(
        1 for link in campaign.destinations if link.enabled and link.destination.enabled
    )
    commissioning = _latest_commissioning(db, organization_id=program.organization_id)
    commissioning_expires_at = aware_utc(commissioning.expires_at) if commissioning else None
    commissioning_current = bool(
        commissioning
        and commissioning.status != ReadinessStatus.BLOCKED
        and commissioning_expires_at
        and commissioning_expires_at > now
    )
    readiness = latest_readiness_report(db, campaign=campaign)
    readiness_current = readiness_is_current(readiness, campaign, now=now)

    blockers: list[str] = []
    warnings: list[str] = []
    if not commissioning_current:
        blockers.append("Нужна актуальная проверка инфраструктуры без блокирующих ошибок")
    if active_destination_count != stage.target_destination_count:
        blockers.append(
            f"Маршрут кампании содержит {active_destination_count} активных назначений, "
            f"для этапа требуется ровно {stage.target_destination_count}"
        )
    if stage.requires_live_telegram and settings.telegram_fake_mode:
        blockers.append(
            "Для live-этапа отключите TELEFLOW_TELEGRAM_FAKE_MODE и перезапустите сервис"
        )
    if not stage.requires_live_telegram and not settings.telegram_fake_mode:
        blockers.append("Локальный этап должен выполняться в fake mode")
    if not approval_is_current(campaign):
        blockers.append("У кампании нет актуального утверждения")
    if not readiness_current:
        blockers.append("Нужен актуальный отчёт Production Pilot")
    elif readiness and readiness.status == ReadinessStatus.WARNING:
        warnings.extend(readiness.warnings)

    if stage.status in {PilotStageStatus.PENDING, PilotStageStatus.READY}:
        stage.status = PilotStageStatus.READY if not blockers else PilotStageStatus.PENDING
        stage.commissioning_check_id = (
            commissioning.id if commissioning_current and commissioning else None
        )
        stage.readiness_report_id = readiness.id if readiness_current and readiness else None
        stage.preflight_report_id = (
            readiness.preflight_report_id if readiness_current and readiness else None
        )

    return {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "active_destination_count": active_destination_count,
        "commissioning_check_id": commissioning.id
        if commissioning_current and commissioning
        else None,
        "readiness_report_id": readiness.id if readiness_current and readiness else None,
        "preflight_report_id": (
            readiness.preflight_report_id if readiness_current and readiness else None
        ),
        "campaign_fingerprint": campaign_fingerprint(campaign),
        "telegram_fake_mode": settings.telegram_fake_mode,
    }


def start_stage(
    db: Session,
    *,
    program: PilotProgram,
    stage: PilotStageExecution,
    started_by: User,
    settings: Settings,
) -> dict[str, Any]:
    """Выполнить операцию start stage. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if program.status in {PilotProgramStatus.COMPLETED, PilotProgramStatus.CANCELLED}:
        raise PilotProgramError("Программа пилота уже завершена")
    if stage.stage_order != program.current_stage_order:
        raise PilotProgramError("Этапы должны выполняться последовательно")
    if stage.status == PilotStageStatus.RUNNING:
        raise PilotProgramError("Этап уже запущен")
    if stage.status == PilotStageStatus.AWAITING_SIGNOFF:
        raise PilotProgramError("Сначала примите или отклоните зафиксированные доказательства")
    if stage.status in {PilotStageStatus.PASSED, PilotStageStatus.SKIPPED}:
        raise PilotProgramError("Этап уже закрыт")
    state = refresh_stage_state(db, program=program, stage=stage, settings=settings)
    if state["blockers"]:
        raise PilotProgramError("; ".join(state["blockers"]))
    if stage.status == PilotStageStatus.FAILED:
        # Explicit restart of a rejected stage begins a new evidence cycle.
        # The previous run ID, evidence hash and decision remain traceable in
        # audit events, while mutable stage state cannot authorize a new attempt.
        stage.campaign_run_id = None
        stage.evidence = {}
        stage.evidence_sha256 = None
        stage.signed_off_by_id = None
        stage.signed_off_at = None
        stage.signoff_note = None
    stage.status = PilotStageStatus.RUNNING
    stage.commissioning_check_id = state["commissioning_check_id"]
    stage.readiness_report_id = state["readiness_report_id"]
    stage.preflight_report_id = state["preflight_report_id"]
    stage.started_by_id = started_by.id
    stage.started_at = utcnow()
    stage.failure_reason = None
    stage.signoff_note = None
    program.status = PilotProgramStatus.ACTIVE
    db.flush()
    return state


def _canonical_evidence(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Реализовать внутренний этап canonical evidence step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return payload, hashlib.sha256(raw).hexdigest()


def _evidence_integrity_error(*, program: PilotProgram, stage: PilotStageExecution) -> str | None:
    """Реализовать внутренний этап evidence integrity error step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    evidence = stage.evidence or {}
    if not evidence and not stage.evidence_sha256:
        if stage.status in {
            PilotStageStatus.AWAITING_SIGNOFF,
            PilotStageStatus.PASSED,
        }:
            return "missing_evidence"
        return None
    if not evidence or not stage.evidence_sha256:
        return "incomplete_evidence"
    _, actual_digest = _canonical_evidence(evidence)
    if not hmac.compare_digest(actual_digest, stage.evidence_sha256):
        return "hash_mismatch"
    evidence_context = {
        "program_id": evidence.get("program_id"),
        "stage_id": evidence.get("stage_id"),
        "campaign_id": evidence.get("campaign_id"),
        "campaign_run_id": (evidence.get("campaign_run") or {}).get("id"),
        "target_destination_count": evidence.get("target_destination_count"),
        "requires_live_telegram": evidence.get("requires_live_telegram"),
    }
    expected_context = {
        "program_id": program.id,
        "stage_id": stage.id,
        "campaign_id": program.campaign_id,
        "campaign_run_id": stage.campaign_run_id,
        "target_destination_count": stage.target_destination_count,
        "requires_live_telegram": stage.requires_live_telegram,
    }
    if evidence_context != expected_context:
        return "context_mismatch"
    return None


def attach_run_evidence(
    db: Session,
    *,
    program: PilotProgram,
    stage: PilotStageExecution,
    campaign_run: CampaignRun,
    note: str | None = None,
) -> dict[str, Any]:
    """Выполнить операцию attach run evidence. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if stage.status not in {PilotStageStatus.RUNNING, PilotStageStatus.AWAITING_SIGNOFF}:
        raise PilotProgramError("Сначала запустите текущий этап")
    if stage.campaign_run_id is not None:
        raise PilotProgramError("Доказательства этого этапа уже зафиксированы и неизменяемы")
    if (
        campaign_run.campaign_id != program.campaign_id
        or campaign_run.organization_id != program.organization_id
    ):
        raise PilotProgramError("Запуск относится к другой кампании или организации")
    run_created_at = aware_utc(campaign_run.created_at)
    stage_started_at = aware_utc(stage.started_at)
    if (
        stage_started_at is not None
        and run_created_at is not None
        and run_created_at < stage_started_at
    ):
        raise PilotProgramError("Нельзя использовать запуск, созданный до начала текущего этапа")
    reused_by = db.scalar(
        select(PilotStageExecution).where(
            PilotStageExecution.campaign_run_id == campaign_run.id,
            PilotStageExecution.id != stage.id,
        )
    )
    if reused_by is not None:
        raise PilotProgramError("Этот запуск уже используется как доказательство другого этапа")
    if campaign_run.status not in TERMINAL_RUN_STATUSES:
        raise PilotProgramError("Запуск кампании ещё не завершён")

    jobs = list(
        db.scalars(
            select(DeliveryJob)
            .where(
                DeliveryJob.run_id == campaign_run.id,
                DeliveryJob.organization_id == program.organization_id,
            )
            .order_by(DeliveryJob.created_at, DeliveryJob.id)
        ).all()
    )
    counts = {status.value: 0 for status in JobStatus}
    for job in jobs:
        counts[job.status.value] = counts.get(job.status.value, 0) + 1
    blockers: list[str] = []
    if len(jobs) != stage.target_destination_count:
        blockers.append(
            f"В запуске {len(jobs)} заданий, для этапа требуется {stage.target_destination_count}"
        )
    if counts.get(JobStatus.WAITING_REVIEW.value, 0):
        blockers.append("Есть задания с неоднозначным результатом, требующие ручной сверки")
    if counts.get(JobStatus.FAILED.value, 0):
        blockers.append("Есть задания, завершившиеся ошибкой")
    if counts.get(JobStatus.CANCELLED.value, 0):
        blockers.append("Есть отменённые задания")
    sent_count = counts.get(JobStatus.SENT.value, 0)
    if sent_count != stage.target_destination_count:
        blockers.append(
            f"Подтверждено отправленных сообщений: {sent_count} из {stage.target_destination_count}"
        )

    audit = verify_audit_chain(db, organization_id=program.organization_id)
    if not audit.valid:
        blockers.append("Hash-chain аудита не прошла проверку")

    evidence_payload = {
        "schema_version": 1,
        "program_id": program.id,
        "stage_id": stage.id,
        "stage_code": stage.code,
        "target_destination_count": stage.target_destination_count,
        "requires_live_telegram": stage.requires_live_telegram,
        "campaign_id": program.campaign_id,
        "campaign_name": program.campaign.name,
        "campaign_fingerprint": campaign_fingerprint(program.campaign),
        "campaign_run": {
            "id": campaign_run.id,
            "status": campaign_run.status.value,
            "scheduled_for": campaign_run.scheduled_for.isoformat(),
            "started_at": campaign_run.started_at.isoformat() if campaign_run.started_at else None,
            "finished_at": campaign_run.finished_at.isoformat()
            if campaign_run.finished_at
            else None,
            "total_jobs": len(jobs),
            "status_counts": counts,
        },
        "deliveries": [
            {
                "job_id": job.id,
                "destination_id": job.destination_id,
                "status": job.status.value,
                "telegram_message_id": job.telegram_message_id,
                "error_code": job.error_code,
                "attempt_count": job.attempt_count,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            }
            for job in jobs
        ],
        "commissioning_check_id": stage.commissioning_check_id,
        "readiness_report_id": stage.readiness_report_id,
        "preflight_report_id": stage.preflight_report_id,
        "audit": {
            "valid": audit.valid,
            "head_sequence": audit.head_sequence,
            "head_hash": audit.computed_head_hash,
            "checked_entries": audit.checked_entries,
        },
        "blockers": blockers,
        "operator_note": note,
        "captured_at": utcnow().isoformat(),
    }
    evidence, digest = _canonical_evidence(evidence_payload)
    stage.campaign_run_id = campaign_run.id
    stage.evidence = evidence
    stage.evidence_sha256 = digest
    stage.status = PilotStageStatus.AWAITING_SIGNOFF
    stage.failure_reason = "; ".join(blockers) if blockers else None
    db.flush()
    return evidence


def signoff_stage(
    db: Session,
    *,
    program: PilotProgram,
    stage: PilotStageExecution,
    signed_by: User,
    decision: str,
    note: str,
) -> None:
    """Выполнить операцию signoff stage. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if stage.status != PilotStageStatus.AWAITING_SIGNOFF:
        raise PilotProgramError("Этап ещё не готов к итоговому решению")
    integrity_error = _evidence_integrity_error(program=program, stage=stage)
    if integrity_error in {"missing_evidence", "incomplete_evidence"}:
        raise PilotProgramError("У этапа отсутствуют проверяемые доказательства")
    if integrity_error == "hash_mismatch":
        raise PilotProgramError("Целостность доказательств этапа нарушена")
    if integrity_error == "context_mismatch":
        raise PilotProgramError("Доказательства не соответствуют текущему этапу пилота")
    if (
        program.require_distinct_signoff
        and stage.requires_live_telegram
        and stage.started_by_id == signed_by.id
    ):
        raise PilotProgramError("Live-этап должен подтвердить другой владелец или администратор")
    blockers = list((stage.evidence or {}).get("blockers") or [])
    if decision == "passed" and blockers:
        raise PilotProgramError(
            "Нельзя принять этап с блокирующими результатами: " + "; ".join(blockers)
        )

    stage.signed_off_by_id = signed_by.id
    stage.signed_off_at = utcnow()
    stage.signoff_note = note
    if decision == "passed":
        stage.status = PilotStageStatus.PASSED
        stage.failure_reason = None
        next_order = stage.stage_order + 1
        next_stage = next((item for item in program.stages if item.stage_order == next_order), None)
        if next_stage is None:
            program.status = PilotProgramStatus.COMPLETED
            program.completed_at = utcnow()
        else:
            program.current_stage_order = next_order
            program.status = PilotProgramStatus.ACTIVE
    else:
        stage.status = PilotStageStatus.FAILED
        stage.failure_reason = note
        program.status = PilotProgramStatus.PAUSED
    db.flush()


def acceptance_report(
    db: Session,
    *,
    program: PilotProgram,
    signing_key=None,
    cipher: SecretCipher | None = None,
) -> tuple[bytes, str, dict[str, Any] | None]:
    """Выполнить операцию acceptance report. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    organization = db.get(Organization, program.organization_id)
    audit = verify_audit_chain(db, organization_id=program.organization_id)
    evidence_integrity_errors: list[dict[str, str]] = []
    for stage in program.stages:
        integrity_error = _evidence_integrity_error(program=program, stage=stage)
        if integrity_error:
            evidence_integrity_errors.append({"stage_id": stage.id, "reason": integrity_error})

    payload = {
        "product": "TeleFlow Platform",
        "report_schema_version": 1,
        "generated_at": utcnow().isoformat(),
        "organization": {
            "id": organization.id if organization else program.organization_id,
            "name": organization.name if organization else None,
        },
        "program": {
            "id": program.id,
            "name": program.name,
            "status": program.status.value,
            "campaign_id": program.campaign_id,
            "campaign_name": program.campaign.name,
            "stage_sizes": program.stage_sizes,
            "require_distinct_signoff": program.require_distinct_signoff,
            "created_at": program.created_at.isoformat(),
            "completed_at": program.completed_at.isoformat() if program.completed_at else None,
        },
        "stages": [
            {
                "id": stage.id,
                "order": stage.stage_order,
                "code": stage.code,
                "title": stage.title,
                "target_destination_count": stage.target_destination_count,
                "requires_live_telegram": stage.requires_live_telegram,
                "status": stage.status.value,
                "commissioning_check_id": stage.commissioning_check_id,
                "readiness_report_id": stage.readiness_report_id,
                "preflight_report_id": stage.preflight_report_id,
                "campaign_run_id": stage.campaign_run_id,
                "evidence_sha256": stage.evidence_sha256,
                "evidence": stage.evidence,
                "started_by_id": stage.started_by_id,
                "started_at": stage.started_at.isoformat() if stage.started_at else None,
                "signed_off_by_id": stage.signed_off_by_id,
                "signed_off_at": stage.signed_off_at.isoformat() if stage.signed_off_at else None,
                "signoff_note": stage.signoff_note,
                "failure_reason": stage.failure_reason,
            }
            for stage in sorted(program.stages, key=lambda item: item.stage_order)
        ],
        "audit_chain": {
            "valid": audit.valid,
            "checked_entries": audit.checked_entries,
            "legacy_entries": audit.legacy_entries,
            "head_sequence": audit.head_sequence,
            "head_hash": audit.computed_head_hash,
            "first_error": audit.first_error,
        },
        "evidence_integrity": {
            "valid": not evidence_integrity_errors,
            "issues": evidence_integrity_errors,
        },
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    wrapper = {
        "manifest": {
            "algorithm": "SHA-256",
            "payload_sha256": digest,
            "generated_at": payload["generated_at"],
        },
        "payload": payload,
    }
    signature = None
    if signing_key is not None:
        if cipher is None:
            raise PilotProgramError("Для подписи акта не инициализирован encryption service")
        signature = sign_bytes(
            canonical,
            key=signing_key,
            cipher=cipher,
            purpose="teleflow.pilot_acceptance.payload.v1",
        )
        wrapper["signature"] = signature
    data = json.dumps(wrapper, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    return data, digest, signature
