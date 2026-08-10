# Recovery Assurance & Continuity 1.8

Документ описывает контур резервного копирования и проверяемого восстановления TeleFlow Platform 1.8. Он дополняет обычное создание backup-файла доказательствами происхождения, проверкой целостности, изолированным restore drill и формальной политикой RPO/RTO.

## 1. Цели

Контур решает четыре разные задачи, которые нельзя смешивать:

1. **Создать backup** базы данных и, при необходимости, локального storage.
2. **Подтвердить происхождение и целостность** через Ed25519-подпись receipt, подписанный manifest и SHA-256 всех файлов.
3. **Проверить восстановимость** в изолированной среде, не изменяя production.
4. **Оценить соответствие RPO/RTO** по фактически проверенным backup и restore drill.

Наличие файла в каталоге само по себе не считается доказательством восстановления.

## 2. Модель состояний backup

Каждый backup evidence имеет отдельный статус:

```text
registered
verified
invalid
expired
deleted
```

`registered` означает только то, что подписанный receipt принят платформой и связан с организацией. Этот статус не подтверждает наличие, SHA-256, manifest или содержимое самого архива.

`verified` присваивается только после локальной проверки:

- подписи receipt;
- принадлежности организации;
- SHA-256 архива;
- безопасной структуры ZIP;
- подписанного manifest;
- размеров и SHA-256 всех заявленных файлов;
- database dump/copy;
- storage claims;
- encryption policy.

Только `verified` backup участвует в расчёте RPO и retained-copy policy.

## 3. Подписанные документы

### 3.1 Backup receipt

Отдельный JSON-файл содержит:

- `backup_id`;
- organization ID;
- время начала и завершения;
- тип и версию базы;
- имя, размер и SHA-256 артефакта;
- сведения о шифровании;
- SHA-256 manifest;
- количество и размер storage-файлов;
- версию приложения и migration head;
- signature envelope Ed25519.

Purpose подписи:

```text
teleflow-recovery-backup-receipt-v1
```

### 3.2 Backup manifest

`MANIFEST.json` находится внутри backup ZIP и описывает точный набор файлов, database/storage claims и SHA-256 содержимого.

Purpose подписи:

```text
teleflow-recovery-backup-manifest-v1
```

Manifest не разрешает незаявленные файлы и не принимает path traversal, абсолютные пути, symlink entries, encrypted ZIP entries или повторяющиеся пути.

### 3.3 Restore-drill receipt

После проверки восстановления создаётся отдельный подписанный JSON:

- `drill_id`;
- `backup_id`;
- режим проверки;
- время и длительность;
- статус;
- RTO target и факт выполнения;
- набор проверок;
- blockers/warnings;
- evidence SHA-256;
- signature envelope.

Purpose подписи:

```text
teleflow-recovery-restore-drill-v1
```

Неуспешный drill также сохраняется как доказательство. Подпись подтверждает происхождение отчёта, но не превращает проваленную проверку в успешную.

## 4. Режимы restore drill

### SQLite isolated

Для SQLite выполняется реальное изолированное восстановление:

1. Архив расшифровывается при необходимости.
2. Проверяются receipt, manifest и все SHA-256.
3. Database copy извлекается во временный каталог.
4. Открывается отдельное SQLite-соединение.
5. Выполняются integrity/schema checks.
6. Рабочий файл БД не открывается на запись и не заменяется.
7. Временная среда удаляется после завершения.

Режим evidence:

```text
sqlite_isolated
```

### PostgreSQL metadata-only

В 1.8 PostgreSQL custom dump проверяется через `pg_restore --list` и криптографический контур. Это подтверждает читаемость dump format и manifest, но не является полноценным восстановлением в отдельный PostgreSQL cluster.

Режим evidence:

```text
metadata_only
```

Для production acceptance требуется внешний drill в отдельной временной БД/кластере с последующим smoke, entity-count и application checks. Платформа не выдаёт metadata-only за полный isolated restore.

### PostgreSQL isolated

Enum `postgres_isolated` зарезервирован для контролируемого восстановления в явно заданную disposable DB. Веб-панель не предоставляет функцию production restore, а 1.8 не выполняет автоматическое создание внешнего PostgreSQL-кластера.

## 5. Политика RPO/RTO

Для организации сохраняются:

- `rpo_hours`;
- `rto_minutes`;
- максимальный возраст успешного drill;
- минимальное количество retained verified backups;
- требование шифрования;
- требование trusted signature;
- обязательность restore drill;
- включённость recovery gate.

Compliance оценивает:

- возраст последнего `verified` backup;
- число проверенных сохранённых backup;
- шифрование последнего backup;
- статус подписи;
- возраст последнего успешного drill;
- фактическую длительность против RTO;
- consistency между backup и drill.

Статус:

```text
passed
warning
blocked
```

В development отсутствие evidence может быть предупреждением. В production обязательные нарушения становятся blockers commissioning.

## 6. Production policy

Production runtime требует:

```env
TELEFLOW_RECOVERY_REQUIRE_ENCRYPTED_BACKUP=true
TELEFLOW_RECOVERY_REQUIRE_TRUSTED_SIGNATURE=true
TELEFLOW_RECOVERY_AGE_RECIPIENT=age1...
TELEFLOW_ARTIFACT_SIGNATURE_POLICY=require_trusted
```

Production не запускается, если обязательное шифрование, trusted receipt или age recipient отключены.

Политика организации в production не позволяет через API отключить:

- recovery assurance;
- encrypted backup;
- trusted signature;
- restore drill.

## 7. Создание backup

```bash
python scripts/recovery_backup.py \
  --organization default \
  --actor-email owner@example.com \
  --output-dir /secure/backups \
  --json
```

Без local storage:

```bash
python scripts/recovery_backup.py \
  --organization default \
  --actor-email owner@example.com \
  --output-dir /secure/backups \
  --without-storage
```

С явным age recipient:

```bash
python scripts/recovery_backup.py \
  --organization default \
  --actor-email owner@example.com \
  --output-dir /secure/backups \
  --age-recipient 'age1...' \
  --json
```

Legacy wrapper остаётся безопасным интерфейсом:

```bash
BACKUP_ROOT=/secure/backups \
RECOVERY_ORGANIZATION=default \
RECOVERY_ACTOR_EMAIL=owner@example.com \
./scripts/backup.sh
```

Команда создаёт артефакт и receipt, но новый evidence остаётся `registered`, пока архив не проверен.

## 8. Проверка архива

```bash
python scripts/recovery_verify.py \
  /secure/backups/teleflow-recovery-....zip.age \
  /secure/backups/teleflow-recovery-....receipt.json \
  --organization default \
  --actor-email owner@example.com \
  --age-identity-file /secure/age/identity.txt \
  --json
```

Для незашифрованного development backup параметр identity не нужен.

Успешная проверка переводит evidence из `registered` в `verified`. Ошибка оставляет его неподтверждённым или помечает invalid; такой файл не удовлетворяет RPO.

## 9. Изолированный restore drill

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

Безопасный shell wrapper:

```bash
RECOVERY_ORGANIZATION=default \
RECOVERY_ACTOR_EMAIL=owner@example.com \
AGE_IDENTITY_FILE=/secure/age/identity.txt \
./scripts/restore.sh artifact.zip.age receipt.json
```

`restore.sh` в 1.8 выполняет только drill. Он не принимает target production database и не выполняет destructive restore.

## 10. Веб-панель

Раздел «Восстановление» показывает:

- RPO/RTO policy;
- compliance checks;
- blockers/warnings;
- зарегистрированные и проверенные backup evidence;
- историю restore drill;
- signature status;
- age и encryption state;
- CLI-команды.

Через веб можно импортировать только подписанные JSON receipts. Raw backup, database dump и age identity через API не загружаются и не скачиваются.

Это снижает риск:

- случайного восстановления в production;
- утечки database dump через browser session;
- хранения age private identity на веб-сервере;
- межтенантного доступа к backup-файлам.

## 11. Commissioning gate

Commissioning 1.8 включает check `recovery_assurance`.

Он учитывает:

- recovery policy;
- последний verified backup;
- последний успешный drill;
- RPO/RTO;
- retained count;
- encryption/signature requirements.

В production blockers recovery-контура блокируют commissioning. В development они отображаются как warnings, чтобы fake mode оставался пригодным для локальной разработки.

## 12. Секреты и escrow

Для полного recovery требуются независимо защищённые:

- `TELEFLOW_MASTER_KEY` для application ciphertext;
- age private identity для backup archive;
- public signing keys/fingerprints;
- PostgreSQL credentials целевой disposable среды;
- S3 recovery credentials, если storage внешний.

Нельзя хранить master key и age private identity только внутри того же backup.

Рекомендуется:

- offline encrypted escrow;
- разделение доступа между двумя ответственными;
- регулярная проверка читаемости escrow;
- журнал выдачи ключей;
- отзыв и ротация после инцидента.

## 13. S3 и внешний storage

Локальный storage может включаться в archive. Для S3 1.8 записывает claims, но не копирует весь bucket через веб/API.

Production recovery plan для S3 должен отдельно включать:

- versioning;
- object lock или immutable backup;
- cross-account/cross-region replication;
- lifecycle;
- inventory/manifest;
- периодическую проверку выбранных объектов;
- credentials для disposable restore environment.

Database backup без object storage не является полным application backup, если сообщения, media или exports хранятся в S3.

## 14. Retention

`recovery_evidence_retention_days` определяет срок хранения metadata/evidence в БД. Физическая retention политика backup storage должна настраиваться отдельно.

Минимальная схема:

```text
несколько ежедневных backup
несколько недельных backup
ежемесячный off-site snapshot
регулярный restore drill
```

Удаление backup допускается только после проверки, что retained verified count и RPO остаются выполненными.

## 15. После disaster restore

Полное production-восстановление выполняется вне веб-панели и только в новой/изолированной среде:

1. Зафиксировать инцидент и остановить Telegram publishers.
2. Сверить SHA-256 и trusted Ed25519 receipt.
3. Расшифровать archive offline.
4. Восстановить PostgreSQL/SQLite в disposable target.
5. Восстановить storage или подключить recovery bucket.
6. Установить точную версию приложения.
7. Выполнить `alembic upgrade head` и `alembic check`.
8. Выполнить `doctor`, audit-chain verify и smoke в fake mode.
9. Проверить jobs `waiting_review`, emergency stop и approvals.
10. Не повторять неопределённые Telegram delivery автоматически.
11. Создать новый commissioning report.
12. Пройти local/service pilot и только затем включать live.

## 16. Ограничения 1.8

- PostgreSQL drill внутри CLI является metadata-only, пока dump не восстановлен в отдельный cluster.
- Система не создаёт облачные snapshots автоматически.
- Система не является immutable/WORM backup appliance.
- Ed25519 подтверждает происхождение в локальной trust-модели, но не заменяет юридически квалифицированную подпись.
- SHA-256 не доказывает, что backup содержит все внешние S3-объекты.
- Recovery evidence не отменяет необходимость операционного restore drill.
- Веб-панель намеренно не выполняет destructive restore.
