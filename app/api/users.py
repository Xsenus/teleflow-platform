from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, require_roles
from app.enums import UserRole
from app.models import User
from app.schemas import MessageResponse, UserCreate, UserRead, UserUpdate
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRead])
def list_users(
    _owner: User = Depends(require_roles(UserRole.OWNER)),
    db: Session = Depends(get_db),
) -> list[User]:
    """Прочитать users. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(User)
            .where(User.organization_id == _owner.organization_id)
            .order_by(User.created_at)
        ).all()
    )


@router.post("", response_model=UserRead, status_code=201)
def create_user(
    payload: UserCreate,
    request: Request,
    owner: User = Depends(require_roles(UserRole.OWNER)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> User:
    """Создать user. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    if db.scalar(select(User.id).where(User.email == payload.email.lower())):
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")
    user = User(
        organization_id=owner.organization_id,
        email=payload.email.lower(),
        display_name=payload.display_name,
        password_hash=hash_password(payload.password, settings),
        role=payload.role,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    write_audit(
        db,
        action="user.created",
        actor=owner,
        entity_type="user",
        entity_id=user.id,
        details={"email": user.email, "role": user.role.value},
        request=request,
    )
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    owner: User = Depends(require_roles(UserRole.OWNER)),
    db: Session = Depends(get_db),
) -> User:
    """Обновить user. Переход применяется только после проверки его предусловий."""
    target = db.scalar(
        select(User).where(User.id == user_id, User.organization_id == owner.organization_id)
    )
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    data = payload.model_dump(exclude_unset=True)
    if target.id == owner.id and data.get("is_active") is False:
        raise HTTPException(status_code=400, detail="Нельзя отключить собственную учётную запись")
    if target.role == UserRole.OWNER and data.get("role") not in {None, UserRole.OWNER}:
        owner_count = (
            db.scalar(
                select(func.count(User.id)).where(
                    User.role == UserRole.OWNER, User.organization_id == owner.organization_id
                )
            )
            or 0
        )
        if owner_count <= 1:
            raise HTTPException(
                status_code=400, detail="В системе должен остаться хотя бы один владелец"
            )
    for key, value in data.items():
        setattr(target, key, value)
    write_audit(
        db,
        action="user.updated",
        actor=owner,
        entity_type="user",
        entity_id=target.id,
        details=data,
        request=request,
    )
    db.commit()
    db.refresh(target)
    return target


@router.delete("/{user_id}", response_model=MessageResponse)
def deactivate_user(
    user_id: str,
    request: Request,
    owner: User = Depends(require_roles(UserRole.OWNER)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Выполнить операцию deactivate user. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    target = db.scalar(
        select(User).where(User.id == user_id, User.organization_id == owner.organization_id)
    )
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.id == owner.id:
        raise HTTPException(status_code=400, detail="Нельзя отключить собственную учётную запись")
    target.is_active = False
    write_audit(
        db,
        action="user.deactivated",
        actor=owner,
        entity_type="user",
        entity_id=target.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Пользователь отключён")
