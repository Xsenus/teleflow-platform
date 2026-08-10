from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_cipher, get_current_user, require_roles
from app.enums import ReleaseTransparencyEventType, SafetySeverity, UserRole
from app.models import (
    ReleaseAttestation,
    ReleaseDependencyAssessment,
    ReleaseTransparencyEvent,
    User,
)
from app.schemas import (
    DependencyAssessmentCreate,
    DependencyInventoryCreate,
    DependencyPolicyPatch,
    DependencyPolicyRead,
    ReleaseDependencyAssessmentRead,
    ReleaseTransparencyEventRead,
    ReleaseTransparencyRequest,
    ReleaseTransparencyVerification,
)
from app.services.crypto import SecretCipher
from app.services.supply_chain import (
    SupplyChainError,
    append_transparency_event,
    build_inventory_report,
    build_release_sbom,
    create_dependency_assessment,
    get_or_create_dependency_policy,
    update_dependency_policy,
    verify_dependency_assessment,
    verify_transparency_chain,
)

router = APIRouter(prefix="/supply-chain", tags=["supply-chain"])


def _attestation_or_404(
    db: Session,
    *,
    item_id: str,
    organization_id: str,
    for_update: bool = False,
) -> ReleaseAttestation:
    """Реализовать внутренний этап attestation or 404 step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stmt = select(ReleaseAttestation).where(
        ReleaseAttestation.id == item_id,
        ReleaseAttestation.organization_id == organization_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    item = db.scalar(stmt)
    if item is None:
        raise HTTPException(status_code=404, detail="Release attestation не найден")
    return item


def _assessment_or_404(
    db: Session,
    *,
    item_id: str,
    organization_id: str,
    for_update: bool = False,
) -> ReleaseDependencyAssessment:
    """Реализовать внутренний этап assessment or 404 step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stmt = select(ReleaseDependencyAssessment).where(
        ReleaseDependencyAssessment.id == item_id,
        ReleaseDependencyAssessment.organization_id == organization_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    item = db.scalar(stmt)
    if item is None:
        raise HTTPException(status_code=404, detail="Dependency assessment не найден")
    return item


@router.get("/policy", response_model=DependencyPolicyRead)
def get_policy(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Прочитать policy. Значение возвращается без несвязанных изменений состояния."""
    policy = get_or_create_dependency_policy(
        db, organization_id=user.organization_id, settings=settings
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.patch("/policy", response_model=DependencyPolicyRead)
def patch_policy(
    payload: DependencyPolicyPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Обновить policy. Переход применяется только после проверки его предусловий."""
    policy = get_or_create_dependency_policy(
        db, organization_id=user.organization_id, settings=settings
    )
    try:
        update_dependency_policy(
            db,
            policy=policy,
            user=user,
            settings=settings,
            values=payload.model_dump(exclude_unset=True),
        )
    except SupplyChainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
        db,
        action="dependency_policy.updated",
        actor=user,
        entity_type="dependency_policy",
        entity_id=policy.id,
        severity=SafetySeverity.WARNING,
        details={
            "require_exact_pins": policy.require_exact_pins,
            "require_vulnerability_scan": policy.require_vulnerability_scan,
            "require_trusted_report": policy.require_trusted_report,
            "max_critical": policy.max_critical,
            "max_high": policy.max_high,
            "max_medium": policy.max_medium,
            "denied_package_count": len(policy.denied_packages or []),
        },
        request=request,
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.get("/assessments", response_model=list[ReleaseDependencyAssessmentRead])
def list_assessments(
    release_attestation_id: str | None = Query(default=None, max_length=36),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Прочитать assessments. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(ReleaseDependencyAssessment).where(
        ReleaseDependencyAssessment.organization_id == user.organization_id
    )
    if release_attestation_id:
        stmt = stmt.where(
            ReleaseDependencyAssessment.release_attestation_id == release_attestation_id
        )
    return list(
        db.scalars(stmt.order_by(ReleaseDependencyAssessment.created_at.desc()).limit(limit)).all()
    )


@router.post(
    "/assessments/{attestation_id}",
    response_model=ReleaseDependencyAssessmentRead,
    status_code=201,
)
def create_assessment(
    attestation_id: str,
    payload: DependencyAssessmentCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
):
    """Создать assessment. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    attestation = _attestation_or_404(
        db,
        item_id=attestation_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    try:
        item = create_dependency_assessment(
            db,
            organization_id=user.organization_id,
            attestation=attestation,
            user=user,
            settings=settings,
            cipher=cipher,
            report=payload.report,
            signature=payload.signature,
            sign_with_default_key=payload.sign_with_default_key,
        )
    except SupplyChainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
        db,
        action="dependency_assessment.created",
        actor=user,
        entity_type="release_dependency_assessment",
        entity_id=item.id,
        severity=(SafetySeverity.CRITICAL if item.blockers else SafetySeverity.WARNING),
        details={
            "release_attestation_id": item.release_attestation_id,
            "report_sha256": item.report_sha256,
            "sbom_sha256": item.sbom_sha256,
            "status": item.status.value,
            "signature_status": item.signature_status.value,
            "critical": item.critical_count,
            "high": item.high_count,
            "medium": item.medium_count,
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post(
    "/assessments/{attestation_id}/inventory",
    response_model=ReleaseDependencyAssessmentRead,
    status_code=201,
)
def create_inventory_assessment(
    attestation_id: str,
    payload: DependencyInventoryCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
):
    """Создать inventory assessment. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    attestation = _attestation_or_404(
        db,
        item_id=attestation_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    report = build_inventory_report(
        attestation,
        scanner_name=payload.scanner_name,
        scanner_version=payload.scanner_version,
    )
    try:
        item = create_dependency_assessment(
            db,
            organization_id=user.organization_id,
            attestation=attestation,
            user=user,
            settings=settings,
            cipher=cipher,
            report=report,
            signature=None,
            sign_with_default_key=True,
        )
    except SupplyChainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
        db,
        action="dependency_assessment.inventory_created",
        actor=user,
        entity_type="release_dependency_assessment",
        entity_id=item.id,
        severity=SafetySeverity.WARNING,
        details={
            "release_attestation_id": item.release_attestation_id,
            "report_sha256": item.report_sha256,
            "status": item.status.value,
            "signature_status": item.signature_status.value,
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post(
    "/assessments/{item_id}/verify",
    response_model=ReleaseDependencyAssessmentRead,
)
def verify_assessment(
    item_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Проверить assessment. Некорректные данные или состояние отклоняются до побочного эффекта."""
    item = _assessment_or_404(
        db,
        item_id=item_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    valid = verify_dependency_assessment(db, item=item, settings=settings)
    write_audit(
        db,
        action="dependency_assessment.verified",
        actor=user,
        entity_type="release_dependency_assessment",
        entity_id=item.id,
        severity=SafetySeverity.INFO if valid else SafetySeverity.CRITICAL,
        details={
            "valid": valid,
            "status": item.status.value,
            "signature_status": item.signature_status.value,
            "report_sha256": item.report_sha256,
            "policy_sha256": item.policy_sha256,
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.get("/assessments/{item_id}/sbom")
def download_sbom(
    item_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Выполнить операцию download sbom. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    item = _assessment_or_404(db, item_id=item_id, organization_id=user.organization_id)
    body = json.dumps(item.sbom_payload, ensure_ascii=False, sort_keys=True, indent=2).encode(
        "utf-8"
    )
    return Response(
        content=body,
        media_type="application/vnd.cyclonedx+json",
        headers={
            "Content-Disposition": f'attachment; filename="teleflow-sbom-{item.id}.cdx.json"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/attestations/{attestation_id}/sbom")
def generate_sbom(
    attestation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Выполнить операцию generate sbom. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    attestation = _attestation_or_404(
        db, item_id=attestation_id, organization_id=user.organization_id
    )
    sbom = build_release_sbom(attestation)
    body = json.dumps(sbom, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    return Response(
        content=body,
        media_type="application/vnd.cyclonedx+json",
        headers={
            "Content-Disposition": f'attachment; filename="teleflow-{attestation.version}.cdx.json"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/transparency", response_model=list[ReleaseTransparencyEventRead])
def list_transparency(
    limit: int = Query(default=500, ge=1, le=5000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Прочитать transparency. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(ReleaseTransparencyEvent)
            .where(ReleaseTransparencyEvent.organization_id == user.organization_id)
            .order_by(ReleaseTransparencyEvent.sequence.desc())
            .limit(limit)
        ).all()
    )


@router.get("/transparency/verify", response_model=ReleaseTransparencyVerification)
def verify_transparency(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Проверить transparency. Некорректные данные или состояние отклоняются до побочного эффекта."""
    return verify_transparency_chain(db, organization_id=user.organization_id)


@router.post(
    "/transparency/{attestation_id}/publish",
    response_model=ReleaseTransparencyEventRead,
    status_code=201,
)
def publish_attestation(
    attestation_id: str,
    payload: ReleaseTransparencyRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    cipher: SecretCipher = Depends(get_cipher),
):
    """Выполнить операцию publish attestation. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    attestation = _attestation_or_404(
        db,
        item_id=attestation_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    try:
        item = append_transparency_event(
            db,
            organization_id=user.organization_id,
            attestation=attestation,
            user=user,
            cipher=cipher,
            event_type=ReleaseTransparencyEventType.PUBLISHED,
            reason=payload.reason,
        )
    except SupplyChainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
        db,
        action="release_transparency.published",
        actor=user,
        entity_type="release_transparency_event",
        entity_id=item.id,
        severity=SafetySeverity.WARNING,
        details={
            "sequence": item.sequence,
            "release_attestation_id": item.release_attestation_id,
            "entry_hash": item.entry_hash,
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post(
    "/transparency/{attestation_id}/withdraw",
    response_model=ReleaseTransparencyEventRead,
    status_code=201,
)
def withdraw_attestation(
    attestation_id: str,
    payload: ReleaseTransparencyRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    cipher: SecretCipher = Depends(get_cipher),
):
    """Выполнить операцию withdraw attestation. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    attestation = _attestation_or_404(
        db,
        item_id=attestation_id,
        organization_id=user.organization_id,
        for_update=True,
    )
    try:
        item = append_transparency_event(
            db,
            organization_id=user.organization_id,
            attestation=attestation,
            user=user,
            cipher=cipher,
            event_type=ReleaseTransparencyEventType.WITHDRAWN,
            reason=payload.reason,
        )
    except SupplyChainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
        db,
        action="release_transparency.withdrawn",
        actor=user,
        entity_type="release_transparency_event",
        entity_id=item.id,
        severity=SafetySeverity.CRITICAL,
        details={
            "sequence": item.sequence,
            "release_attestation_id": item.release_attestation_id,
            "entry_hash": item.entry_hash,
            "reason": payload.reason,
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item
