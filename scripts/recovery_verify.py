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
from app.services.recovery import RecoveryError, verify_recovery_archive
from scripts.recovery_common import resolve_actor, resolve_organization


def main() -> None:
    """Запустить the recovery verify command-line workflow. Arguments, exit status and user-visible
    diagnostics are handled here.
    """
    parser = argparse.ArgumentParser(
        description="Проверка подписи, manifest и содержимого TeleFlow recovery backup"
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--organization", help="Slug или UUID организации")
    parser.add_argument("--actor-email", help="Email Owner/Admin")
    parser.add_argument("--age-identity-file", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    identity = args.age_identity_file or settings.recovery_age_identity_file
    try:
        receipt_data = args.receipt.read_bytes()
    except OSError as exc:
        raise SystemExit(f"Receipt не читается: {exc}") from exc

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        with session_factory() as db:
            organization = resolve_organization(db, args.organization)
            actor = resolve_actor(db, organization, args.actor_email)
            try:
                payload, manifest, checks = verify_recovery_archive(
                    db,
                    artifact_path=args.artifact,
                    receipt_data=receipt_data,
                    organization_id=organization.id,
                    actor=actor,
                    settings=settings,
                    age_identity_file=identity,
                )
                db.commit()
            except RecoveryError as exc:
                db.rollback()
                raise SystemExit(str(exc)) from exc
    finally:
        engine.dispose()

    result = {
        "ok": True,
        "backup_id": payload.backup_id,
        "artifact_sha256": payload.artifact.sha256,
        "manifest_file_count": len(manifest.files),
        "checks": checks,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Backup {payload.backup_id}: VALID")
        for check in checks:
            print(f"[{check['status'].upper()}] {check['code']}: {check['message']}")


if __name__ == "__main__":
    main()
