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
from app.services.recovery import RecoveryError, create_restore_drill
from scripts.recovery_common import resolve_actor, resolve_organization


def main() -> None:
    """Запустить the recovery restore drill command-line workflow. Arguments, exit status and user-
    visible diagnostics are handled here.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Изолированный restore drill TeleFlow. Команда никогда не заменяет рабочую базу данных."
        )
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--organization", help="Slug или UUID организации")
    parser.add_argument("--actor-email", help="Email Owner/Admin")
    parser.add_argument("--output-dir", type=Path, help="Каталог drill receipt")
    parser.add_argument("--age-identity-file", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    identity = args.age_identity_file or settings.recovery_age_identity_file
    output_dir = args.output_dir or (settings.backups_path / "recovery-drills")
    try:
        receipt_data = args.receipt.read_bytes()
    except OSError as exc:
        raise SystemExit(f"Receipt не читается: {exc}") from exc

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    cipher = SecretCipher(settings.master_key)
    try:
        with session_factory() as db:
            organization = resolve_organization(db, args.organization)
            actor = resolve_actor(db, organization, args.actor_email)
            try:
                result = create_restore_drill(
                    db,
                    artifact_path=args.artifact,
                    receipt_data=receipt_data,
                    organization=organization,
                    actor=actor,
                    settings=settings,
                    cipher=cipher,
                    output_dir=output_dir,
                    age_identity_file=identity,
                )
                db.commit()
            except RecoveryError as exc:
                db.rollback()
                raise SystemExit(str(exc)) from exc
    finally:
        engine.dispose()

    payload = {
        "ok": result.drill.status.value == "passed",
        "drill_id": result.drill.drill_id,
        "backup_id": result.drill.backup_id,
        "status": result.drill.status.value,
        "mode": result.drill.mode.value,
        "duration_seconds": result.drill.duration_seconds,
        "rto_met": result.drill.rto_met,
        "receipt": str(result.receipt_path),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Restore drill: {result.drill.status.value}")
        print(f"Режим: {result.drill.mode.value}")
        print(f"Длительность: {result.drill.duration_seconds} сек.")
        print(f"RTO соблюдён: {'да' if result.drill.rto_met else 'нет'}")
        print(f"Receipt: {result.receipt_path}")
    raise SystemExit(0 if result.drill.status.value == "passed" else 2)


if __name__ == "__main__":
    main()
