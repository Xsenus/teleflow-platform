from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.audit import backfill_audit_chain, verify_audit_chain
from app.config import get_settings
from app.database import create_database_engine, create_session_factory
from app.models import Organization


def _targets(
    db: Any, organization: str | None, include_system: bool
) -> list[tuple[str | None, str]]:
    """Реализовать внутренний этап targets step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    stmt = select(Organization).order_by(Organization.slug)
    organizations = list(db.scalars(stmt).all())
    if organization:
        organizations = [
            item for item in organizations if item.id == organization or item.slug == organization
        ]
        if not organizations:
            raise SystemExit(f"Организация {organization!r} не найдена")
    result: list[tuple[str | None, str]] = [(item.id, item.slug) for item in organizations]
    if include_system:
        result.append((None, "__system__"))
    return result


def _result_payload(label: str, result: Any) -> dict[str, Any]:
    """Реализовать внутренний этап result payload step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    payload = result.to_dict()
    payload["organization"] = label
    return payload


def main() -> None:
    """Запустить the audit chain command-line workflow. Arguments, exit status and user-visible
    diagnostics are handled here.
    """
    parser = argparse.ArgumentParser(
        description="Проверка и offline-backfill tamper-evident audit hash-chain TeleFlow"
    )
    parser.add_argument("command", choices=["verify", "backfill"])
    parser.add_argument(
        "--organization",
        help="Organization slug или UUID. По умолчанию обрабатываются все организации.",
    )
    parser.add_argument(
        "--include-system",
        action="store_true",
        help="Дополнительно проверить системную цепочку без organization_id.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Обязательное подтверждение для backfill.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.command == "backfill" and not args.yes:
        parser.error(
            "backfill переписывает только chain metadata и должен выполняться при остановленных API/worker; "
            "повторите с --yes после создания backup"
        )

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    output: list[dict[str, Any]] = []
    success = True
    try:
        with session_factory() as db:
            for organization_id, label in _targets(db, args.organization, args.include_system):
                if args.command == "backfill":
                    result = backfill_audit_chain(db, organization_id=organization_id)
                    db.commit()
                else:
                    result = verify_audit_chain(db, organization_id=organization_id)
                output.append(_result_payload(label, result))
                success = success and result.valid
    finally:
        engine.dispose()

    if args.json:
        print(json.dumps({"ok": success, "results": output}, ensure_ascii=False, indent=2))
    else:
        for item in output:
            marker = "OK" if item["valid"] else "FAIL"
            print(
                f"[{marker}] {item['organization']}: entries={item['checked_entries']} "
                f"legacy={item['legacy_entries']} head={item['head_sequence']}"
            )
            if item.get("first_error"):
                print(
                    f"  error={item['first_error']} entry={item.get('first_error_entry_id') or '—'}"
                )
            print(f"  head_hash={item['computed_head_hash']}")
        print("AUDIT CHAIN VALID" if success else "AUDIT CHAIN INVALID")
    raise SystemExit(0 if success else 2)


if __name__ == "__main__":
    main()
