from __future__ import annotations

import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.enums import ConnectionKind, UserRole
from app.models import (
    AIProviderConfig,
    AutomationFlow,
    AutomationPolicy,
    KnowledgeBaseArticle,
    TelegramConnection,
    User,
)
from app.schemas import (
    AIProviderCreate,
    AIProviderPatch,
    AIProviderRead,
    AIProviderTestResponse,
    AutomationFlowCreate,
    AutomationFlowPatch,
    AutomationFlowRead,
    AutomationPolicyCreate,
    AutomationPolicyPatch,
    AutomationPolicyRead,
    KnowledgeArticleCreate,
    KnowledgeArticlePatch,
    KnowledgeArticleRead,
    MessageResponse,
)
from app.services.ai.service import AIService
from app.services.url_security import UnsafeURL, validate_outbound_url

router = APIRouter(prefix="/automation", tags=["automation"])


def _connection(db: Session, connection_id: str, org_id: str) -> TelegramConnection:
    """Реализовать внутренний этап connection step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    item = db.scalar(
        select(TelegramConnection).where(
            TelegramConnection.id == connection_id,
            TelegramConnection.organization_id == org_id,
            TelegramConnection.kind == ConnectionKind.BOT,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Bot API-подключение не найдено")
    return item


def _provider(db: Session, provider_id: str, org_id: str) -> AIProviderConfig:
    """Реализовать внутренний этап provider step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    item = db.scalar(
        select(AIProviderConfig).where(
            AIProviderConfig.id == provider_id,
            AIProviderConfig.organization_id == org_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="AI-провайдер не найден")
    return item


def _flow(
    db: Session,
    flow_id: str,
    org_id: str,
    *,
    require_active: bool = False,
) -> AutomationFlow:
    """Реализовать внутренний этап flow step. Вспомогательная функция сохраняет детерминированность
    и тестируемость процесса.
    """
    item = db.scalar(
        select(AutomationFlow).where(
            AutomationFlow.id == flow_id,
            AutomationFlow.organization_id == org_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Сценарий автоматизации не найден")
    if require_active and not item.is_active:
        raise HTTPException(
            status_code=409,
            detail="Сначала активируйте сценарий автоматизации",
        )
    return item


def _policy(db: Session, policy_id: str, org_id: str) -> AutomationPolicy:
    """Реализовать внутренний этап policy step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    item = db.scalar(
        select(AutomationPolicy).where(
            AutomationPolicy.id == policy_id,
            AutomationPolicy.organization_id == org_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Политика автоматизации не найдена")
    return item


@router.get("/flows", response_model=list[AutomationFlowRead])
def list_flows(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AutomationFlow]:
    """Прочитать flows. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(AutomationFlow)
            .where(AutomationFlow.organization_id == user.organization_id)
            .order_by(AutomationFlow.updated_at.desc())
        ).all()
    )


@router.get("/flows/{flow_id}", response_model=AutomationFlowRead)
def get_flow(
    flow_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AutomationFlow:
    """Прочитать flow. Значение возвращается без несвязанных изменений состояния."""
    return _flow(db, flow_id, user.organization_id)


@router.post("/flows", response_model=AutomationFlowRead, status_code=201)
def create_flow(
    payload: AutomationFlowCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> AutomationFlow:
    """Создать flow. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    item = AutomationFlow(
        organization_id=user.organization_id,
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
        revision=1,
        definition=payload.definition.model_dump(mode="json"),
        created_by_id=user.id,
    )
    db.add(item)
    write_audit(
        db,
        actor=user,
        action="automation.flow_created",
        entity_type="automation_flow",
        entity_id=item.id,
        details={
            "active": item.is_active,
            "nodes": len(payload.definition.nodes),
        },
        request=request,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Сценарий с таким именем уже существует"
        ) from exc
    db.refresh(item)
    return item


@router.patch("/flows/{flow_id}", response_model=AutomationFlowRead)
def patch_flow(
    flow_id: str,
    payload: AutomationFlowPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> AutomationFlow:
    """Обновить flow. Переход применяется только после проверки его предусловий."""
    item = _flow(db, flow_id, user.organization_id)
    values = payload.model_dump(exclude_unset=True)
    definition = values.pop("definition", None)
    requested_active = values.get("is_active")
    if definition is not None and item.is_active:
        raise HTTPException(
            status_code=409,
            detail="Активный сценарий неизменяем. Сначала отключите его или создайте копию",
        )
    if requested_active is False and item.is_active:
        active_policy = db.scalar(
            select(AutomationPolicy.id).where(
                AutomationPolicy.organization_id == user.organization_id,
                AutomationPolicy.automation_flow_id == item.id,
                AutomationPolicy.enabled.is_(True),
            )
        )
        if active_policy:
            raise HTTPException(
                status_code=409,
                detail="Сценарий используется включённой политикой. Сначала отключите политику",
            )
    for key, value in values.items():
        setattr(item, key, value)
    if definition is not None:
        item.definition = definition.model_dump(mode="json")
        item.revision += 1
    write_audit(
        db,
        actor=user,
        action="automation.flow_updated",
        entity_type="automation_flow",
        entity_id=item.id,
        details={"fields": sorted(payload.model_fields_set), "revision": item.revision},
        request=request,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Сценарий с таким именем уже существует"
        ) from exc
    db.refresh(item)
    return item


@router.delete("/flows/{flow_id}", response_model=MessageResponse)
def delete_flow(
    flow_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete flow. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    item = _flow(db, flow_id, user.organization_id)
    linked_policy = db.scalar(
        select(AutomationPolicy.id).where(
            AutomationPolicy.organization_id == user.organization_id,
            AutomationPolicy.automation_flow_id == item.id,
        )
    )
    if linked_policy:
        raise HTTPException(
            status_code=409,
            detail="Сценарий привязан к политике. Сначала отвяжите его",
        )
    db.delete(item)
    write_audit(
        db,
        actor=user,
        action="automation.flow_deleted",
        entity_type="automation_flow",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Сценарий удалён")


@router.get("/policies", response_model=list[AutomationPolicyRead])
def list_policies(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AutomationPolicy]:
    """Прочитать policies. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(AutomationPolicy)
            .where(AutomationPolicy.organization_id == user.organization_id)
            .order_by(AutomationPolicy.created_at.desc())
        ).all()
    )


@router.post("/policies", response_model=AutomationPolicyRead, status_code=201)
def create_policy(
    payload: AutomationPolicyCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> AutomationPolicy:
    """Создать policy. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    _connection(db, payload.telegram_connection_id, user.organization_id)
    if payload.ai_provider_config_id:
        _provider(db, payload.ai_provider_config_id, user.organization_id)
    if payload.automation_flow_id:
        _flow(
            db,
            payload.automation_flow_id,
            user.organization_id,
            require_active=True,
        )
    try:
        ZoneInfo(payload.timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Неизвестный часовой пояс IANA") from exc
    if payload.enabled:
        active = db.scalar(
            select(AutomationPolicy.id).where(
                AutomationPolicy.organization_id == user.organization_id,
                AutomationPolicy.telegram_connection_id == payload.telegram_connection_id,
                AutomationPolicy.enabled.is_(True),
            )
        )
        if active:
            raise HTTPException(
                status_code=409,
                detail="Для этого бота уже включена политика автоматизации",
            )
    item = AutomationPolicy(
        organization_id=user.organization_id,
        created_by_id=user.id,
        **payload.model_dump(),
    )
    db.add(item)
    write_audit(
        db,
        actor=user,
        action="automation.policy_created",
        entity_type="automation_policy",
        entity_id=item.id,
        request=request,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Политика с таким именем уже существует"
        ) from exc
    db.refresh(item)
    return item


@router.patch("/policies/{policy_id}", response_model=AutomationPolicyRead)
def patch_policy(
    policy_id: str,
    payload: AutomationPolicyPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> AutomationPolicy:
    """Обновить policy. Переход применяется только после проверки его предусловий."""
    item = _policy(db, policy_id, user.organization_id)
    values = payload.model_dump(exclude_unset=True)
    if values.get("ai_provider_config_id"):
        _provider(db, values["ai_provider_config_id"], user.organization_id)
    if "automation_flow_id" in values and values["automation_flow_id"] is not None:
        _flow(
            db,
            values["automation_flow_id"],
            user.organization_id,
            require_active=True,
        )
    if values.get("timezone_name"):
        try:
            ZoneInfo(values["timezone_name"])
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=422, detail="Неизвестный часовой пояс IANA") from exc
    if values.get("enabled") is True:
        active = db.scalar(
            select(AutomationPolicy.id).where(
                AutomationPolicy.organization_id == user.organization_id,
                AutomationPolicy.telegram_connection_id == item.telegram_connection_id,
                AutomationPolicy.enabled.is_(True),
                AutomationPolicy.id != item.id,
            )
        )
        if active:
            raise HTTPException(
                status_code=409, detail="Для этого бота уже включена другая политика"
            )
    for key, value in values.items():
        setattr(item, key, value)
    write_audit(
        db,
        actor=user,
        action="automation.policy_updated",
        entity_type="automation_policy",
        entity_id=item.id,
        details={"fields": sorted(values)},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.delete("/policies/{policy_id}", response_model=MessageResponse)
def delete_policy(
    policy_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete policy. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    item = _policy(db, policy_id, user.organization_id)
    if item.enabled:
        raise HTTPException(status_code=409, detail="Сначала отключите политику")
    db.delete(item)
    write_audit(
        db,
        actor=user,
        action="automation.policy_deleted",
        entity_type="automation_policy",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Политика удалена")


@router.get("/knowledge", response_model=list[KnowledgeArticleRead])
def list_knowledge(
    vacancy_key: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[KnowledgeBaseArticle]:
    """Прочитать knowledge. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(KnowledgeBaseArticle).where(
        KnowledgeBaseArticle.organization_id == user.organization_id
    )
    if vacancy_key:
        stmt = stmt.where(KnowledgeBaseArticle.vacancy_key == vacancy_key)
    return list(db.scalars(stmt.order_by(KnowledgeBaseArticle.updated_at.desc())).all())


@router.post("/knowledge", response_model=KnowledgeArticleRead, status_code=201)
def create_knowledge(
    payload: KnowledgeArticleCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> KnowledgeBaseArticle:
    """Создать knowledge. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    item = KnowledgeBaseArticle(
        organization_id=user.organization_id,
        created_by_id=user.id,
        **payload.model_dump(),
    )
    db.add(item)
    write_audit(
        db,
        actor=user,
        action="knowledge.created",
        entity_type="knowledge_article",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.patch("/knowledge/{article_id}", response_model=KnowledgeArticleRead)
def patch_knowledge(
    article_id: str,
    payload: KnowledgeArticlePatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> KnowledgeBaseArticle:
    """Обновить knowledge. Переход применяется только после проверки его предусловий."""
    item = db.scalar(
        select(KnowledgeBaseArticle).where(
            KnowledgeBaseArticle.id == article_id,
            KnowledgeBaseArticle.organization_id == user.organization_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Статья не найдена")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(item, key, value)
    item.revision += 1
    write_audit(
        db,
        actor=user,
        action="knowledge.updated",
        entity_type="knowledge_article",
        entity_id=item.id,
        details={"revision": item.revision},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.delete("/knowledge/{article_id}", response_model=MessageResponse)
def delete_knowledge(
    article_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete knowledge. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    item = db.scalar(
        select(KnowledgeBaseArticle).where(
            KnowledgeBaseArticle.id == article_id,
            KnowledgeBaseArticle.organization_id == user.organization_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Статья не найдена")
    db.delete(item)
    write_audit(
        db,
        actor=user,
        action="knowledge.deleted",
        entity_type="knowledge_article",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Статья удалена")


@router.get("/providers", response_model=list[AIProviderRead])
def list_providers(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AIProviderConfig]:
    """Прочитать providers. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(AIProviderConfig)
            .where(AIProviderConfig.organization_id == user.organization_id)
            .order_by(AIProviderConfig.created_at.desc())
        ).all()
    )


@router.post("/providers", response_model=AIProviderRead, status_code=201)
def create_provider(
    payload: AIProviderCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> AIProviderConfig:
    """Создать provider. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    settings = request.app.state.settings
    if payload.kind.value not in settings.ai_provider_allowlist_values:
        raise HTTPException(status_code=422, detail="Провайдер запрещён системным allowlist")
    base_url = str(payload.base_url).rstrip("/") if payload.base_url else None
    if base_url:
        try:
            validate_outbound_url(
                base_url,
                allow_private=settings.allow_private_integration_urls,
                require_https=settings.is_production,
            )
        except UnsafeURL as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = AIProviderConfig(
        organization_id=user.organization_id,
        name=payload.name,
        kind=payload.kind,
        base_url=base_url,
        model_name=payload.model_name,
        api_key_enc=None,
        enabled=payload.enabled,
        timeout_seconds=payload.timeout_seconds,
        max_output_tokens=payload.max_output_tokens,
        temperature_milli=int(payload.temperature * 1000),
        data_region=payload.data_region,
        system_prompt=payload.system_prompt,
        allowed_models=payload.allowed_models,
        created_by_id=user.id,
    )
    db.add(item)
    db.flush()
    if payload.api_key:
        item.api_key_enc = request.app.state.cipher.encrypt(
            payload.api_key, context=f"ai-provider:{item.id}:api-key"
        )
    write_audit(
        db,
        actor=user,
        action="ai.provider_created",
        entity_type="ai_provider",
        entity_id=item.id,
        details={"kind": item.kind.value, "model": item.model_name},
        request=request,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Провайдер с таким именем уже существует"
        ) from exc
    db.refresh(item)
    return item


@router.patch("/providers/{provider_id}", response_model=AIProviderRead)
def patch_provider(
    provider_id: str,
    payload: AIProviderPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> AIProviderConfig:
    """Обновить provider. Переход применяется только после проверки его предусловий."""
    item = _provider(db, provider_id, user.organization_id)
    values = payload.model_dump(exclude_unset=True)
    api_key = values.pop("api_key", None)
    if values.get("base_url"):
        values["base_url"] = str(values["base_url"]).rstrip("/")
        try:
            validate_outbound_url(
                values["base_url"],
                allow_private=request.app.state.settings.allow_private_integration_urls,
                require_https=request.app.state.settings.is_production,
            )
        except UnsafeURL as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "temperature" in values:
        values["temperature_milli"] = int(values.pop("temperature") * 1000)
    for key, value in values.items():
        setattr(item, key, value)
    if api_key:
        item.api_key_enc = request.app.state.cipher.encrypt(
            api_key, context=f"ai-provider:{item.id}:api-key"
        )
    write_audit(
        db,
        actor=user,
        action="ai.provider_updated",
        entity_type="ai_provider",
        entity_id=item.id,
        details={"fields": sorted(values)},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/providers/{provider_id}/test", response_model=AIProviderTestResponse)
def test_provider(
    provider_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> AIProviderTestResponse:
    """Проверить сценарий provider. Тест завершается ошибкой при нарушении зафиксированного
    инварианта.
    """
    item = _provider(db, provider_id, user.organization_id)
    service = AIService(request.app.state.settings, request.app.state.cipher)
    started = time.perf_counter()
    try:
        provider = service._build_provider(item)  # isolated health probe, no conversation data
        result = provider.generate(
            message="Меня зовут Тест, я из города Хельсинки, опыт 2 года.",
            history=[],
            knowledge=[],
            candidate={},
            system_prompt=item.system_prompt,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Проверка провайдера не пройдена: {exc}"
        ) from exc
    latency = int((time.perf_counter() - started) * 1000)
    write_audit(
        db,
        actor=user,
        action="ai.provider_tested",
        entity_type="ai_provider",
        entity_id=item.id,
        details={"ok": True, "latency_ms": latency},
        request=request,
    )
    db.commit()
    return AIProviderTestResponse(
        ok=True,
        provider=provider.name,
        model=provider.model_name,
        sample=result.reply_text[:500],
        latency_ms=latency,
    )


@router.delete("/providers/{provider_id}", response_model=MessageResponse)
def delete_provider(
    provider_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete provider. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    item = _provider(db, provider_id, user.organization_id)
    linked = db.scalar(
        select(AutomationPolicy.id)
        .where(AutomationPolicy.ai_provider_config_id == item.id)
        .limit(1)
    )
    if linked:
        raise HTTPException(status_code=409, detail="Провайдер используется политикой")
    db.delete(item)
    write_audit(
        db,
        actor=user,
        action="ai.provider_deleted",
        entity_type="ai_provider",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="AI-провайдер удалён")
