from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.database import create_database_engine, create_session_factory
from app.models import RecoveryPolicy
from app.services.artifact_signing import generate_signing_key, get_default_signing_key
from app.services.crypto import SecretCipher
from app.services.recovery import (
    RecoveryError,
    create_recovery_backup,
    create_restore_drill,
    verify_recovery_archive,
)
from scripts.recovery_common import resolve_actor, resolve_organization


def main() -> None:
    """Запустить the recovery smoke command-line workflow. Arguments, exit status and user-visible
    diagnostics are handled here.
    """
    parser = argparse.ArgumentParser(
        description="Offline recovery smoke for test/development environments"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--organization")
    parser.add_argument("--actor-email")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.is_production:
        raise SystemExit("recovery_smoke.py запрещён в production")
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, mode=0o700)

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    cipher = SecretCipher(settings.master_key)
    try:
        with session_factory() as db:
            organization = resolve_organization(db, args.organization)
            actor = resolve_actor(db, organization, args.actor_email)
            key = get_default_signing_key(db, organization_id=organization.id, require_private=True)
            if key is None:
                key = generate_signing_key(
                    db,
                    organization_id=organization.id,
                    created_by=actor,
                    cipher=cipher,
                    name="Recovery smoke key",
                    make_default=True,
                    trusted_for_import=True,
                    note="Автоматический ключ из release QA; не используется в production",
                )
            policy = (
                db.query(RecoveryPolicy).filter_by(organization_id=organization.id).one_or_none()
            )
            if policy is None:
                policy = RecoveryPolicy(
                    organization_id=organization.id,
                    enabled=True,
                    rpo_hours=24,
                    rto_minutes=60,
                    restore_drill_max_age_days=30,
                    minimum_retained_backups=1,
                    require_encrypted_backup=False,
                    require_trusted_signature=True,
                    require_restore_drill=True,
                    created_by_id=actor.id,
                    updated_by_id=actor.id,
                )
                db.add(policy)
            else:
                policy.minimum_retained_backups = 1
                policy.require_encrypted_backup = False
                policy.require_trusted_signature = True
                policy.require_restore_drill = True
                policy.updated_by_id = actor.id
            db.flush()
            backup = create_recovery_backup(
                db,
                organization=organization,
                actor=actor,
                settings=settings,
                cipher=cipher,
                output_dir=output_dir / "backups",
                include_storage=False,
            )
            receipt_data = backup.receipt_path.read_bytes()
            payload, manifest, checks = verify_recovery_archive(
                db,
                artifact_path=backup.artifact_path,
                receipt_data=receipt_data,
                organization_id=organization.id,
                actor=actor,
                settings=settings,
            )
            drill = create_restore_drill(
                db,
                artifact_path=backup.artifact_path,
                receipt_data=receipt_data,
                organization=organization,
                actor=actor,
                settings=settings,
                cipher=cipher,
                output_dir=output_dir / "drills",
            )
            db.commit()
    except RecoveryError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        engine.dispose()

    result = {
        "ok": drill.drill.status.value == "passed",
        "backup_id": payload.backup_id,
        "backup_status": backup.evidence.status.value,
        "artifact": str(backup.artifact_path),
        "receipt": str(backup.receipt_path),
        "manifest_file_count": len(manifest.files),
        "verification_checks": [item["code"] for item in checks],
        "drill_id": drill.drill.drill_id,
        "drill_status": drill.drill.status.value,
        "drill_mode": drill.drill.mode.value,
        "rto_met": drill.drill.rto_met,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Recovery smoke: {'PASS' if result['ok'] else 'FAIL'}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
