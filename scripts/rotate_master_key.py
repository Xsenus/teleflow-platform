from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.database import create_database_engine, create_session_factory
from app.services.crypto import SecretCipher
from app.services.key_rotation import rotate_master_key
from app.services.storage import StorageService


def _read_new_key(args: argparse.Namespace) -> str:
    """Реализовать внутренний этап read new key step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    values: list[str] = []
    if args.new_key_env:
        value = os.environ.get(args.new_key_env)
        if value:
            values.append(value.strip())
    if args.new_key_file:
        values.append(Path(args.new_key_file).read_text(encoding="utf-8").strip())
    values = [item for item in values if item]
    if len(values) != 1:
        raise SystemExit(
            "Передайте новый ключ ровно одним способом: через переменную окружения "
            "--new-key-env или защищённый файл --new-key-file"
        )
    return values[0]


def main() -> None:
    """Запустить the rotate master key command-line workflow. Arguments, exit status and user-
    visible diagnostics are handled here.
    """
    parser = argparse.ArgumentParser(
        description="Offline-ротация TELEFLOW_MASTER_KEY для всех зашифрованных данных"
    )
    parser.add_argument(
        "--new-key-env",
        default="TELEFLOW_NEW_MASTER_KEY",
        help="Имя переменной окружения с новым ключом",
    )
    parser.add_argument(
        "--new-key-file",
        help="Путь к файлу с новым ключом; при использовании укажите --new-key-env ''",
    )
    parser.add_argument("--dry-run", action="store_true", help="Только проверить расшифрование")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Подтверждение остановки API/worker и наличия проверенного backup",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.dry_run and not args.yes:
        parser.error(
            "ротация выполняется только при остановленных API/worker и после backup; повторите с --yes"
        )

    settings = get_settings()
    new_key = _read_new_key(args)
    if new_key == settings.master_key:
        raise SystemExit("Новый master key совпадает с текущим")
    if not new_key.startswith("base64:") and len(new_key) < 32:
        raise SystemExit(
            "Новый master key должен быть случайным и не короче 32 символов "
            "или иметь формат base64: с 32 байтами"
        )

    old_cipher = SecretCipher(settings.master_key)
    try:
        new_cipher = SecretCipher(new_key)
    except (ValueError, UnicodeError) as exc:
        raise SystemExit(f"Некорректный новый master key: {exc}") from exc
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    storage = StorageService(settings)
    try:
        with session_factory() as db:
            try:
                report = rotate_master_key(
                    db,
                    old_cipher=old_cipher,
                    new_cipher=new_cipher,
                    storage=storage,
                    dry_run=args.dry_run,
                )
                if args.dry_run:
                    db.rollback()
                else:
                    db.commit()
            except Exception:
                db.rollback()
                raise
    finally:
        engine.dispose()

    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        mode = "PREFLIGHT OK" if args.dry_run else "ROTATION COMPLETE"
        print(mode)
        print(f"Values: {payload['total_values']}")
        for label, count in payload["fields"].items():
            print(f"  {label}: {count}")
        print(f"  privacy_exports: {payload['privacy_exports']}")
        if not args.dry_run:
            print(
                "До запуска сервисов замените TELEFLOW_MASTER_KEY на новый ключ и удалите "
                "TELEFLOW_NEW_MASTER_KEY из окружения. Старый ключ храните только в защищённом backup escrow."
            )


if __name__ == "__main__":
    main()
