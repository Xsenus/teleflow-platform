# TeleFlow Platform 1.8.0 — Recovery Assurance & Continuity

Дата релиза: 7 августа 2026 года.

Версия 1.8 добавляет проверяемый контур резервного копирования и восстановления поверх Artifact Trust 1.7. Основное изменение — разделение факта существования backup receipt, криптографической проверки архива и фактического restore drill.

## Основные возможности

- tenant-scoped recovery policy с RPO, RTO, drill age и retained-copy requirements;
- состояния backup evidence `registered / verified / invalid / expired / deleted`;
- подписанный Ed25519 backup receipt;
- подписанный `MANIFEST.json` внутри архива;
- SHA-256 каждого файла и всего артефакта;
- optional/required age encryption;
- безопасная ZIP-проверка без path traversal, symlink, duplicates и undeclared files;
- реальный isolated SQLite restore drill;
- честный PostgreSQL metadata-only drill через `pg_restore --list`;
- подписанный restore-drill receipt с RTO evidence;
- production enforcement trusted signature + age encryption;
- metadata-only web API и 25-й раздел панели «Восстановление»;
- commissioning check `recovery_assurance`;
- safe CLI и non-destructive `restore.sh` wrapper;
- migration head `4c8e0a2b6d1f`;
- отдельный recovery smoke в release-QA.

## Критическое изменение restore.sh

Скрипт `scripts/restore.sh` больше не выполняет восстановление в указанную рабочую БД. В 1.8 он принимает только:

```text
artifact
receipt
```

и запускает изолированный restore drill. Это намеренное несовместимое изменение, устраняющее риск случайного destructive restore.

Production disaster recovery выполняется по документированному runbook в новой среде, а не одной shell-командой из веб-приложения.

## Registered не равно verified

Импорт JSON receipt в веб-панель создаёт evidence со статусом `registered`.

Он не удовлетворяет RPO, пока CLI не проверит:

- сам archive;
- archive SHA-256;
- receipt signature;
- manifest signature;
- database/storage claims;
- все file hashes;
- encryption policy.

Только после этого evidence становится `verified`.

## Security hardening

- PostgreSQL password не передаётся в argv `pg_dump`, а используется через `PGPASSWORD` environment;
- production не запускается с отключёнными encrypted backup или trusted receipts;
- age private identity не загружается через API;
- raw backup не хранится в web storage;
- импорт receipt привязан к organization ID и защищён RBAC/CSRF/audit;
- future timestamps, tampered receipt и cross-tenant evidence отклоняются;
- failed drill остаётся подписанным доказательством, но не выполняет RPO/RTO;
- corrupt artifact нельзя повысить до `verified`;
- backup directory исключается из local-storage snapshot, symlink блокируется.

## Миграция

```text
1.7: 3b7d9f1a2c4e
1.8: 4c8e0a2b6d1f
```

Перед обновлением:

1. Создать backup версии 1.7 существующим способом.
2. Сохранить master key, signing public keys/fingerprints и age identity отдельно.
3. Остановить API/worker.
4. Обновить код и зависимости.
5. Выполнить `alembic upgrade head`.
6. Настроить recovery policy.
7. Создать новый 1.8 backup, выполнить verify и restore drill.

Rollback migration до 1.7 удаляет только таблицы recovery metadata. Физические архивы и JSON receipts следует сохранить отдельно.

## Совместимость

Все функции 1.7 сохраняются:

- Bot API и MTProto publisher;
- controlled operations;
- approval governance;
- production pilot;
- commissioning;
- configuration portability;
- Ed25519 artifact trust;
- inbound Telegram Business/AI automation;
- analytics, privacy, integrations и audit chain.

## Ограничения

- PostgreSQL полный isolated restore в disposable cluster остаётся внешней production-процедурой; встроенный drill 1.8 не выдаёт metadata check за полное восстановление.
- S3 bucket contents не копируются автоматически в local backup.
- Docker image build и live Telegram acceptance зависят от целевой инфраструктуры.
- Backup evidence не заменяет off-site immutable storage и регулярную человеческую проверку recovery plan.

Полная модель: [RECOVERY_ASSURANCE.md](RECOVERY_ASSURANCE.md). Процедуры: [BACKUP_RESTORE.md](BACKUP_RESTORE.md).
