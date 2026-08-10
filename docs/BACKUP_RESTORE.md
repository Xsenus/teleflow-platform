# Backup, verify и restore drill — TeleFlow Platform 1.8

## 1. Принцип безопасности

В версии 1.8 создание backup, проверка архива и восстановление разделены на три независимых действия:

```text
create backup
→ verify artifact
→ isolated restore drill
```

Веб-панель хранит только metadata и подписанные JSON receipts. Она не принимает raw database dump, age identity и не выполняет production restore.

`registered` receipt не равен `verified` backup. В RPO учитывается только архив, полностью проверенный локальным CLI.

## 2. Что входит в backup

Для SQLite/local storage:

- consistent SQLite backup API copy;
- local storage tree без вложенного backup directory;
- `MANIFEST.json`;
- SHA-256 каждого файла;
- Ed25519 signature manifest;
- отдельный Ed25519-signed backup receipt.

Для PostgreSQL:

- custom-format dump через `pg_dump`;
- local storage при его использовании;
- подписанный manifest и receipt.

Для S3 archive содержит database и claims внешнего storage. Сам bucket должен защищаться отдельными versioning/replication/snapshot controls.

## 3. Требования

Development SQLite:

```text
Python dependencies проекта
organization signing key с private part
доступ к database/storage
```

PostgreSQL:

```text
pg_dump
pg_restore
```

Encrypted production backup:

```text
age CLI
публичный age recipient на сервере backup
private age identity в отдельном offline escrow
trusted Ed25519 signing key
```

## 4. Создание backup

```bash
python scripts/recovery_backup.py \
  --organization default \
  --actor-email owner@example.com \
  --output-dir /secure/backups \
  --json
```

Исключить local storage:

```bash
python scripts/recovery_backup.py \
  --organization default \
  --actor-email owner@example.com \
  --output-dir /secure/backups \
  --without-storage
```

Явно указать age recipient:

```bash
python scripts/recovery_backup.py \
  --organization default \
  --actor-email owner@example.com \
  --output-dir /secure/backups \
  --age-recipient 'age1...' \
  --json
```

Совместимый shell wrapper:

```bash
BACKUP_ROOT=/secure/backups \
RECOVERY_ORGANIZATION=default \
RECOVERY_ACTOR_EMAIL=owner@example.com \
BACKUP_AGE_RECIPIENT='age1...' \
./scripts/backup.sh
```

Результат:

```text
teleflow-recovery-<id>.zip или .zip.age
teleflow-recovery-<id>.receipt.json
```

Receipt можно импортировать в панель, но до verify он имеет статус `registered`.

## 5. Проверка backup

Незашифрованный development archive:

```bash
python scripts/recovery_verify.py \
  /secure/backups/teleflow-recovery-....zip \
  /secure/backups/teleflow-recovery-....receipt.json \
  --organization default \
  --actor-email owner@example.com \
  --json
```

Encrypted archive:

```bash
python scripts/recovery_verify.py \
  /secure/backups/teleflow-recovery-....zip.age \
  /secure/backups/teleflow-recovery-....receipt.json \
  --organization default \
  --actor-email owner@example.com \
  --age-identity-file /secure/age/identity.txt \
  --json
```

Проверяются:

- organization binding;
- receipt Ed25519 signature/trust policy;
- artifact SHA-256 и размер;
- age encryption requirement;
- ZIP path safety;
- отсутствие symlink/encrypted/duplicate entries;
- отсутствие незаявленных файлов;
- manifest Ed25519 signature;
- SHA-256 и размер каждого файла;
- database claims;
- storage claims.

Только после полного успеха evidence становится `verified`.

## 6. Изолированный restore drill

```bash
python scripts/recovery_restore_drill.py \
  /secure/backups/teleflow-recovery-....zip.age \
  /secure/backups/teleflow-recovery-....receipt.json \
  --organization default \
  --actor-email owner@example.com \
  --output-dir /secure/recovery-evidence \
  --age-identity-file /secure/age/identity.txt \
  --json
```

Безопасный wrapper:

```bash
RECOVERY_ORGANIZATION=default \
RECOVERY_ACTOR_EMAIL=owner@example.com \
AGE_IDENTITY_FILE=/secure/age/identity.txt \
./scripts/restore.sh \
  /secure/backups/teleflow-recovery-....zip.age \
  /secure/backups/teleflow-recovery-....receipt.json
```

**Важно:** `restore.sh` не заменяет production database. Он всегда запускает drill-only процедуру.

## 7. SQLite drill

SQLite восстанавливается во временный каталог. Проверяется открытие отдельной copy и integrity/schema evidence. Рабочий database file не изменяется.

Успешный режим:

```text
sqlite_isolated
```

## 8. PostgreSQL drill

Встроенная проверка 1.8 выполняет:

```bash
pg_restore --list dump
```

и сохраняет режим:

```text
metadata_only
```

Это не полный restore. Production drill дополнительно выполняется вручную в disposable PostgreSQL database:

1. Создать отдельный database/cluster с отдельными credentials.
2. Выполнить `pg_restore` только туда.
3. Запустить точную версию приложения в fake mode.
4. Выполнить `alembic check`, `doctor`, audit verify и HTTP smoke.
5. Сверить entity counts и выборочные ciphertext/media.
6. Зафиксировать начало, окончание, RTO и blockers.
7. Уничтожить disposable environment.

Никогда не используйте production DSN как target drill.

## 9. RPO/RTO policy

Настройки по умолчанию:

```env
TELEFLOW_RECOVERY_DEFAULT_RPO_HOURS=24
TELEFLOW_RECOVERY_DEFAULT_RTO_MINUTES=60
TELEFLOW_RECOVERY_DEFAULT_DRILL_MAX_AGE_DAYS=30
TELEFLOW_RECOVERY_DEFAULT_MINIMUM_RETAINED_BACKUPS=3
TELEFLOW_RECOVERY_EVIDENCE_RETENTION_DAYS=365
```

Production:

```env
TELEFLOW_RECOVERY_REQUIRE_ENCRYPTED_BACKUP=true
TELEFLOW_RECOVERY_REQUIRE_TRUSTED_SIGNATURE=true
TELEFLOW_RECOVERY_AGE_RECIPIENT=age1...
TELEFLOW_RECOVERY_AGE_IDENTITY_FILE=/run/secrets/teleflow-age-identity.txt
```

Private identity не должна находиться в repository, image или том же backup archive.

## 10. Рекомендуемое расписание

Минимальный production baseline:

```text
backup: ежедневно или чаще RPO
verify: каждый созданный backup
restore drill: не реже policy drill age
retention: минимум policy count + off-site copies
full PostgreSQL disposable restore: регулярно по операционному календарю
```

Пример cron/systemd timer должен вызывать создание, затем verify. Ошибка любого шага должна формировать операторское событие вне того же сервера.

## 11. Retention и off-site

Храните минимум:

- несколько ежедневных verified backup;
- недельные/месячные точки;
- копию в отдельном аккаунте или регионе;
- immutable/object-lock copy;
- публичные signing fingerprints;
- master-key и age-key escrow отдельно.

Не удаляйте старый backup, пока новый не получил `verified` и retained count после удаления остаётся достаточным.

## 12. Master-key rotation

Перед rotation:

1. Создать encrypted backup.
2. Выполнить verify и drill.
3. Сохранить старый master key в offline rollback escrow.
4. Остановить writers.
5. Выполнить rotation dry-run.
6. Выполнить rotation.
7. Создать новый backup и повторить verify/drill.

Age encryption key должен быть независим от `TELEFLOW_MASTER_KEY`.

## 13. Signing keys

Signing private key хранится в БД в AES-256-GCM ciphertext. Для recovery signing capability нужны:

- database backup;
- правильный master key;
- public PEM/fingerprint;
- список revoked keys.

После restore сначала проверьте historical artifacts публичным ключом, затем тестовую подпись новым support bundle. Потерянный private key нельзя восстановить из public PEM.

## 14. Controlled operations после восстановления

До запуска worker:

1. Сохранить organization publishing paused.
2. Проверить jobs `waiting_review` и ambiguous deliveries.
3. Не раскрывать `held` batches автоматически.
4. Отменить просроченные approvals.
5. Выполнить новый preflight/readiness/commissioning.
6. Повторно проверить destination permissions и expiry.
7. Запустить fake campaign.
8. Выполнить service canary.
9. Пройти formal pilot от текущего разрешённого масштаба.

Состояние database до сетевого таймаута не доказывает, что Telegram не принял сообщение. Не выполняйте автоматический retry ambiguous delivery.

## 15. Configuration/support bundles

- Configuration bundle переносит справочную конфигурацию без credentials и не является backup.
- Support bundle содержит обезличенную диагностику и не является backup.
- Pilot acceptance report является signed evidence и не содержит database state.

Для disaster recovery нужен согласованный database + storage backup и внешние ключи.

## 16. Веб-панель

Раздел «Восстановление» позволяет:

- настроить RPO/RTO policy;
- увидеть compliance;
- импортировать backup receipt;
- импортировать drill receipt;
- просмотреть evidence.

Панель намеренно не позволяет:

- загрузить database dump;
- скачать backup;
- указать production target DSN;
- выполнить restore;
- передать age private identity.

## 17. Аварийная процедура полного восстановления

Полный restore выполняется отдельной уполномоченной командой:

1. Объявить incident и запретить Telegram publication.
2. Выбрать последний verified backup в пределах RPO.
3. Независимо сверить trusted signing fingerprint.
4. Расшифровать archive в защищённой disposable среде.
5. Восстановить database/storage.
6. Выполнить migration/schema/application checks.
7. Проверить audit-chain и secrets decryptability.
8. Сменить credentials, которые могли быть скомпрометированы.
9. Создать новый commissioning report.
10. Пройти service canary и staged pilot.
11. Зафиксировать фактический RPO/RTO и postmortem.

## 18. Ограничения

- Встроенный PostgreSQL drill 1.8 — metadata-only.
- Local archive не гарантирует полный S3 recovery.
- Platform metadata не заменяет WORM/immutable storage.
- Ed25519 trust зависит от независимой сверки fingerprint.
- Backup может содержать PII, удалённые после даты снимка; доступ и retention должны соответствовать политике данных.
- Ни одна CLI-команда не должна запускаться с непроверенным target production DSN.

Подробная модель: [RECOVERY_ASSURANCE.md](RECOVERY_ASSURANCE.md).
