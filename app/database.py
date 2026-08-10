from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Base


def create_database_engine(settings: Settings) -> Engine:
    """Создать database engine. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    connect_args: dict[str, Any] = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        database_file = settings.database_url.rsplit("/", 1)[-1]
        if database_file and database_file != ":memory:":
            Path(database_file).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )

    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
            """Обновить sqlite pragma. Переход применяется только после проверки его предусловий."""
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Создать session factory. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def initialize_database(engine: Engine) -> None:
    """Выполнить операцию initialize database. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    Base.metadata.create_all(bind=engine)


def get_db(request: Request) -> Generator[Session, None, None]:
    """Прочитать db. Значение возвращается без несвязанных изменений состояния."""
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
