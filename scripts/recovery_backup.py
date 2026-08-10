from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.database import create_database_engine, create_session_factory
from app.services.crypto import SecretCipher
from app.services.recovery import RecoveryError, create_recovery_backup
from scripts.recovery_common import resolve_actor, resolve_organization


def main() -> None:
    """Запустить the recovery backup command-line workflow. Arguments, exit status and user-visible
    diagnostics are handled here.
    """
    parser = argparse.ArgumentParser(
        description="Создание подписанного TeleFlow backup и безопасного recovery receipt"
    )
    parser.add_argument("--organization", help="Slug или UUID организации")
    parser.add_argument("--actor-email", help="Email Owner/Admin для audit attribution")
    parser.add_argument("--output-dir", type=Path, help="Каталог вывода")
    parser.add_argument(
        "--without-storage",
        action="store_true",
        help="Не включать local storage в backup",
    )
    parser.add_argument(
        "--age-recipient",
        help="Публичный age recipient; переопределяет TELEFLOW_RECOVERY_AGE_RECIPIENT",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    output_dir = args.output_dir or (settings.backups_path / "recovery")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    cipher = SecretCipher(settings.master_key)
    try:
        with session_factory() as db:
            organization = resolve_organization(db, args.organization)
            actor = resolve_actor(db, organization, args.actor_email)
            try:
                result = create_recovery_backup(
                    db,
                    organization=organization,
                    actor=actor,
                    settings=settings,
                    cipher=cipher,
                    output_dir=output_dir,
                    include_storage=not args.without_storage,
                    age_recipient=args.age_recipient,
                )
                db.commit()
            except RecoveryError as exc:
                db.rollback()
                raise SystemExit(str(exc)) from exc
    finally:
        engine.dispose()

    payload = {
        "ok": True,
        "backup_id": result.evidence.backup_id,
        "artifact": str(result.artifact_path),
        "receipt": str(result.receipt_path),
        "artifact_sha256": result.evidence.artifact_sha256,
        "encrypted": result.evidence.artifact_encrypted,
        "receipt_status": result.evidence.status.value,
        "signature_status": result.evidence.signature_status.value,
        "next_step": "Run recovery_verify.py or recovery_restore_drill.py before counting this backup toward RPO",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Backup создан: {result.artifact_path}")
        print(f"Receipt: {result.receipt_path}")
        print(f"SHA-256: {result.evidence.artifact_sha256}")
        print(f"Подпись: {result.evidence.signature_status.value}")
        print(
            "Статус: receipt зарегистрирован; выполните verify или restore drill для подтверждения архива"
        )


if __name__ == "__main__":
    main()
