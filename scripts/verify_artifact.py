#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.artifact_signing import parse_public_key, public_fingerprint
from app.services.artifact_verifier import ArtifactVerificationError, inspect_artifact


def build_parser() -> argparse.ArgumentParser:
    """Создать parser. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    parser = argparse.ArgumentParser(description="Offline-проверка подписанного артефакта TeleFlow")
    parser.add_argument("artifact", type=Path, help="ZIP или JSON артефакт")
    parser.add_argument(
        "--trusted-fingerprint",
        action="append",
        default=[],
        help="Доверенный SHA-256 fingerprint Ed25519 (можно повторять)",
    )
    parser.add_argument(
        "--trusted-public-key",
        action="append",
        type=Path,
        default=[],
        help="PEM или Base64 публичный ключ Ed25519 (можно повторять)",
    )
    parser.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="Не считать отсутствие подписи ошибкой",
    )
    parser.add_argument(
        "--require-trusted",
        action="store_true",
        help="Требовать совпадение подписи с переданным доверенным ключом",
    )
    parser.add_argument("--json", action="store_true", help="Машиночитаемый JSON")
    return parser


def main() -> int:
    """Запустить the verify artifact command-line workflow. Arguments, exit status and user-visible
    diagnostics are handled here.
    """
    args = build_parser().parse_args()
    trusted = {value.strip().lower() for value in args.trusted_fingerprint if value.strip()}
    try:
        for path in args.trusted_public_key:
            trusted.add(public_fingerprint(parse_public_key(path.read_text(encoding="utf-8"))))
        result = inspect_artifact(args.artifact.read_bytes(), trusted_fingerprints=trusted)
    except (OSError, ArtifactVerificationError, ValueError) as exc:
        output = {"valid": False, "error": str(exc)}
        print(json.dumps(output, ensure_ascii=False, indent=2) if args.json else f"ERROR: {exc}")
        return 2

    signature = result["signature"]
    accepted = bool(result["integrity_valid"])
    if args.require_trusted:
        accepted = accepted and bool(signature.get("trusted"))
    elif not args.allow_unsigned:
        accepted = accepted and bool(signature.get("cryptographically_valid"))
    result["valid"] = accepted
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Тип: {result['artifact_type']}")
        print(f"SHA-256: {result['sha256']}")
        print(f"Целостность: {'OK' if result['integrity_valid'] else 'FAIL'}")
        print(f"Подпись: {signature.get('status')}")
        print(f"Fingerprint: {signature.get('fingerprint') or '-'}")
        print(f"Доверие: {'trusted' if signature.get('trusted') else 'not trusted'}")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
