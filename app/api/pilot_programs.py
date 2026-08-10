from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_current_user, require_roles
from app.enums import (
    ArtifactSignatureStatus,
    PilotProgramStatus,
    PilotStageStatus,
    SafetySeverity,
    UserRole,
)
from app.models import (
    Campaign,
    CampaignDestination,
    CampaignRun,
    PilotProgram,
    PilotStageExecution,
    User,
    utcnow,
)
from app.schemas import (
    MessageResponse,
    PilotProgramCreate,
    PilotProgramRead,
    PilotStageAttachRunRequest,
    PilotStageSignoffRequest,
)
from app.services.artifact_signing import get_default_signing_key
from app.services.pilot_programs import (
    PilotProgramError,
    acceptance_report,
    attach_run_evidence,
    create_program,
    load_program,
    refresh_stage_state,
    signoff_stage,
    start_stage,
)

router = APIRouter(prefix="/pilot/programs", tags=["pilot-programs"])


def _load_campaign(db: Session, campaign_id: str, organization_id: str) -> Campaign:
    """Реализовать внутренний этап load campaign step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    campaign = db.scalar(
        select(Campaign)
        .where(Campaign.id == campaign_id, Campaign.organization_id == organization_id)
        .options(
            selectinload(Campaign.connection),
            selectinload(Campaign.template),
            selectinload(Campaign.secondary_template),
            selectinload(Campaign.destinations).selectinload(CampaignDestination.destination),
            selectinload(Campaign.approval_requests),
        )
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    return campaign


def _program_or_404(
    db: Session,
    *,
    program_id: str,
    organization_id: str,
    for_update: bool = False,
) -> PilotProgram:
    """Реализовать внутренний этап program or 404 step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    program = load_program(
        db,
        program_id=program_id,
        organization_id=organization_id,
        for_update=for_update,
    )
    if program is None:
        raise HTTPException(status_code=404, detail="Программа пилота не найдена")
    return program


def _stage_or_404(program: PilotProgram, stage_id: str) -> PilotStageExecution:
    """Реализовать внутренний этап stage or 404 step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stage = next((item for item in program.stages if item.id == stage_id), None)
    if stage is None:
        raise HTTPException(status_code=404, detail="Этап пилота не найден")
    return stage


def _pilot_error(exc: PilotProgramError) -> HTTPException:
    """Реализовать внутренний этап pilot error step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return HTTPException(status_code=409, detail=str(exc))


@router.get("", response_model=list[PilotProgramRead])
def list_pilot_programs(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PilotProgram]:
    """Прочитать pilot programs. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(PilotProgram)
            .where(PilotProgram.organization_id == user.organization_id)
            .options(selectinload(PilotProgram.stages))
            .order_by(PilotProgram.created_at.desc())
        )
        .unique()
        .all()
    )


@router.post("", response_model=PilotProgramRead, status_code=201)
def create_pilot_program(
    payload: PilotProgramCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> PilotProgram:
    """Создать pilot program. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    campaign = _load_campaign(db, payload.campaign_id, user.organization_id)
    try:
        program = create_program(
            db,
            organization_id=user.organization_id,
            campaign=campaign,
            name=payload.name,
            stage_sizes=payload.stage_sizes,
            require_distinct_signoff=payload.require_distinct_signoff,
            notes=payload.notes,
            created_by=user,
        )
    except PilotProgramError as exc:
        raise _pilot_error(exc) from exc
    write_audit(
        db,
        action="pilot_program.created",
        actor=user,
        entity_type="pilot_program",
        entity_id=program.id,
        details={
            "campaign_id": campaign.id,
            "stage_sizes": payload.stage_sizes,
            "require_distinct_signoff": payload.require_distinct_signoff,
        },
        request=request,
    )
    db.commit()
    return _program_or_404(db, program_id=program.id, organization_id=user.organization_id)


@router.get("/{program_id}", response_model=PilotProgramRead)
def get_pilot_program(
    program_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PilotProgram:
    """Прочитать pilot program. Значение возвращается без несвязанных изменений состояния."""
    return _program_or_404(db, program_id=program_id, organization_id=user.organization_id)


@router.post("/{program_id}/stages/{stage_id}/refresh", response_model=PilotProgramRead)
def refresh_pilot_stage(
    program_id: str,
    stage_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> PilotProgram:
    """Выполнить операцию refresh pilot stage. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    program = _program_or_404(
        db,
        program_id=program_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    stage = _stage_or_404(program, stage_id)
    state = refresh_stage_state(db, program=program, stage=stage, settings=settings)
    write_audit(
        db,
        action="pilot_stage.refreshed",
        actor=user,
        entity_type="pilot_stage_execution",
        entity_id=stage.id,
        severity=SafetySeverity.WARNING if state["blockers"] else SafetySeverity.INFO,
        details={
            "program_id": program.id,
            "ready": state["ready"],
            "blockers": state["blockers"],
            "warnings": state["warnings"],
        },
        request=request,
    )
    db.commit()
    return _program_or_404(db, program_id=program.id, organization_id=user.organization_id)


@router.post("/{program_id}/stages/{stage_id}/start", response_model=PilotProgramRead)
def start_pilot_stage(
    program_id: str,
    stage_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> PilotProgram:
    """Выполнить операцию start pilot stage. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    program = _program_or_404(
        db,
        program_id=program_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    stage = _stage_or_404(program, stage_id)
    try:
        state = start_stage(
            db,
            program=program,
            stage=stage,
            started_by=user,
            settings=settings,
        )
    except PilotProgramError as exc:
        raise _pilot_error(exc) from exc
    write_audit(
        db,
        action="pilot_stage.started",
        actor=user,
        entity_type="pilot_stage_execution",
        entity_id=stage.id,
        details={
            "program_id": program.id,
            "stage_order": stage.stage_order,
            "target_destination_count": stage.target_destination_count,
            "commissioning_check_id": state["commissioning_check_id"],
            "readiness_report_id": state["readiness_report_id"],
            "preflight_report_id": state["preflight_report_id"],
        },
        request=request,
    )
    db.commit()
    return _program_or_404(db, program_id=program.id, organization_id=user.organization_id)


@router.post("/{program_id}/stages/{stage_id}/attach-run", response_model=PilotProgramRead)
def attach_pilot_run(
    program_id: str,
    stage_id: str,
    payload: PilotStageAttachRunRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> PilotProgram:
    """Выполнить операцию attach pilot run. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    program = _program_or_404(
        db,
        program_id=program_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    stage = _stage_or_404(program, stage_id)
    campaign_run = db.scalar(
        select(CampaignRun).where(
            CampaignRun.id == payload.campaign_run_id,
            CampaignRun.organization_id == user.organization_id,
        )
    )
    if campaign_run is None:
        raise HTTPException(status_code=404, detail="Запуск кампании не найден")
    try:
        evidence = attach_run_evidence(
            db,
            program=program,
            stage=stage,
            campaign_run=campaign_run,
            note=payload.note,
        )
    except PilotProgramError as exc:
        raise _pilot_error(exc) from exc
    write_audit(
        db,
        action="pilot_stage.evidence_attached",
        actor=user,
        entity_type="pilot_stage_execution",
        entity_id=stage.id,
        severity=(SafetySeverity.WARNING if evidence.get("blockers") else SafetySeverity.INFO),
        details={
            "program_id": program.id,
            "campaign_run_id": campaign_run.id,
            "evidence_sha256": stage.evidence_sha256,
            "blockers": evidence.get("blockers") or [],
        },
        request=request,
    )
    db.commit()
    return _program_or_404(db, program_id=program.id, organization_id=user.organization_id)


@router.post("/{program_id}/stages/{stage_id}/signoff", response_model=PilotProgramRead)
def signoff_pilot_stage(
    program_id: str,
    stage_id: str,
    payload: PilotStageSignoffRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> PilotProgram:
    """Выполнить операцию signoff pilot stage. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    program = _program_or_404(
        db,
        program_id=program_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    stage = _stage_or_404(program, stage_id)
    try:
        signoff_stage(
            db,
            program=program,
            stage=stage,
            signed_by=user,
            decision=payload.decision,
            note=payload.note,
        )
    except PilotProgramError as exc:
        raise _pilot_error(exc) from exc
    write_audit(
        db,
        action="pilot_stage.signed_off",
        actor=user,
        entity_type="pilot_stage_execution",
        entity_id=stage.id,
        severity=(SafetySeverity.INFO if payload.decision == "passed" else SafetySeverity.WARNING),
        details={
            "program_id": program.id,
            "decision": payload.decision,
            "stage_order": stage.stage_order,
            "program_status": program.status.value,
            "evidence_sha256": stage.evidence_sha256,
        },
        request=request,
    )
    db.commit()
    return _program_or_404(db, program_id=program.id, organization_id=user.organization_id)


@router.post("/{program_id}/cancel", response_model=MessageResponse)
def cancel_pilot_program(
    program_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить cancel pilot program. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    program = _program_or_404(
        db,
        program_id=program_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    if program.status == PilotProgramStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Завершённую программу отменить нельзя")
    program.status = PilotProgramStatus.CANCELLED
    program.completed_at = utcnow()
    for stage in program.stages:
        if stage.status in {
            PilotStageStatus.PENDING,
            PilotStageStatus.READY,
            PilotStageStatus.RUNNING,
            PilotStageStatus.AWAITING_SIGNOFF,
        }:
            stage.status = PilotStageStatus.SKIPPED
            stage.failure_reason = "Программа пилота отменена оператором"
    write_audit(
        db,
        action="pilot_program.cancelled",
        actor=user,
        entity_type="pilot_program",
        entity_id=program.id,
        severity=SafetySeverity.WARNING,
        details={"campaign_id": program.campaign_id},
        request=request,
    )
    db.commit()
    return MessageResponse(message="Программа пилота отменена")


@router.get("/{program_id}/acceptance-report")
def download_acceptance_report(
    program_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    """Выполнить операцию download acceptance report. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    program = _program_or_404(db, program_id=program_id, organization_id=user.organization_id)
    signing_key = get_default_signing_key(
        db, organization_id=user.organization_id, require_private=True
    )
    if signing_key is None and settings.artifact_signature_policy != "optional":
        raise HTTPException(
            status_code=409,
            detail="Для акта приёмки требуется активный основной Ed25519-ключ",
        )
    if (
        signing_key is not None
        and settings.artifact_signature_policy == "require_trusted"
        and not signing_key.trusted_for_import
    ):
        raise HTTPException(
            status_code=409,
            detail="Основной Ed25519-ключ должен быть доверен организацией",
        )
    data, digest, signature = acceptance_report(
        db,
        program=program,
        signing_key=signing_key,
        cipher=request.app.state.cipher,
    )
    filename = f"teleflow-pilot-{program.id}-acceptance.json"
    signature_status = (
        ArtifactSignatureStatus.VALID_TRUSTED.value
        if signing_key and signing_key.trusted_for_import
        else ArtifactSignatureStatus.VALID_UNTRUSTED.value
        if signing_key
        else ArtifactSignatureStatus.UNSIGNED.value
    )
    write_audit(
        db,
        action="pilot_program.acceptance_report_downloaded",
        actor=user,
        entity_type="pilot_program",
        entity_id=program.id,
        details={
            "status": program.status.value,
            "campaign_id": program.campaign_id,
            "payload_sha256": digest,
            "signature_status": signature_status,
            "signer_fingerprint": signing_key.fingerprint if signing_key else None,
        },
        request=request,
    )
    db.commit()
    return Response(
        content=data,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-SHA256": digest,
            "X-Artifact-Signature-Status": signature_status,
            "X-Artifact-Signer-Fingerprint": (signing_key.fingerprint if signing_key else ""),
            "Cache-Control": "no-store",
        },
    )
