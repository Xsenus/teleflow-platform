from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.enums import UserRole
from app.models import Campaign, MediaAsset, MessageTemplate, User
from app.schemas import MessageResponse, TemplateCreate, TemplatePatch, TemplateRead

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateRead])
def list_templates(
    active_only: bool = False,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MessageTemplate]:
    """Прочитать templates. Значение возвращается без несвязанных изменений состояния."""
    stmt = (
        select(MessageTemplate)
        .where(MessageTemplate.organization_id == _user.organization_id)
        .order_by(MessageTemplate.updated_at.desc())
    )
    if active_only:
        stmt = stmt.where(MessageTemplate.is_active.is_(True))
    return list(db.scalars(stmt).all())


@router.post("", response_model=TemplateRead, status_code=201)
def create_template(
    payload: TemplateCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> MessageTemplate:
    """Создать template. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    if payload.media_asset_id and not db.scalar(
        select(MediaAsset).where(
            MediaAsset.id == payload.media_asset_id,
            MediaAsset.organization_id == user.organization_id,
        )
    ):
        raise HTTPException(status_code=404, detail="Медиафайл не найден")
    template = MessageTemplate(
        organization_id=user.organization_id,
        name=payload.name,
        body=payload.body,
        parse_mode=payload.parse_mode,
        media_asset_id=payload.media_asset_id,
        link_preview=payload.link_preview,
        created_by_id=user.id,
    )
    db.add(template)
    db.flush()
    write_audit(
        db,
        action="template.created",
        actor=user,
        entity_type="message_template",
        entity_id=template.id,
        details={"name": template.name, "parse_mode": template.parse_mode.value},
        request=request,
    )
    db.commit()
    db.refresh(template)
    return template


@router.get("/{template_id}", response_model=TemplateRead)
def get_template(
    template_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageTemplate:
    """Прочитать template. Значение возвращается без несвязанных изменений состояния."""
    template = db.scalar(
        select(MessageTemplate).where(
            MessageTemplate.id == template_id,
            MessageTemplate.organization_id == _user.organization_id,
        )
    )
    if not template:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    return template


@router.patch("/{template_id}", response_model=TemplateRead)
def patch_template(
    template_id: str,
    payload: TemplatePatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> MessageTemplate:
    """Обновить template. Переход применяется только после проверки его предусловий."""
    template = db.scalar(
        select(MessageTemplate).where(
            MessageTemplate.id == template_id,
            MessageTemplate.organization_id == user.organization_id,
        )
    )
    if not template:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    data = payload.model_dump(exclude_unset=True)
    if "media_asset_id" in data and data["media_asset_id"]:
        if not db.scalar(
            select(MediaAsset).where(
                MediaAsset.id == data["media_asset_id"],
                MediaAsset.organization_id == user.organization_id,
            )
        ):
            raise HTTPException(status_code=404, detail="Медиафайл не найден")
    effective_media_id = data.get("media_asset_id", template.media_asset_id)
    effective_body = data.get("body", template.body)
    if effective_media_id and len(effective_body) > 1024:
        raise HTTPException(
            status_code=422,
            detail="Подпись к медиа не может превышать 1024 символа",
        )
    content_fields = {"body", "parse_mode", "media_asset_id", "link_preview"}
    if content_fields.intersection(data):
        template.revision += 1
    for key, value in data.items():
        setattr(template, key, value)
    write_audit(
        db,
        action="template.updated",
        actor=user,
        entity_type="message_template",
        entity_id=template.id,
        details={"fields": sorted(data), "revision": template.revision},
        request=request,
    )
    db.commit()
    db.refresh(template)
    return template


@router.delete("/{template_id}", response_model=MessageResponse)
def delete_template(
    template_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete template. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    template = db.scalar(
        select(MessageTemplate).where(
            MessageTemplate.id == template_id,
            MessageTemplate.organization_id == user.organization_id,
        )
    )
    if not template:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    campaigns = (
        db.scalar(
            select(func.count(Campaign.id)).where(
                Campaign.template_id == template.id,
                Campaign.organization_id == user.organization_id,
            )
        )
        or 0
    )
    if campaigns:
        template.is_active = False
        message = "Шаблон используется кампаниями и поэтому был отключён"
    else:
        db.delete(template)
        message = "Шаблон удалён"
    write_audit(
        db,
        action="template.deleted_or_disabled",
        actor=user,
        entity_type="message_template",
        entity_id=template.id,
        details={"campaign_count": campaigns},
        request=request,
    )
    db.commit()
    return MessageResponse(message=message)
