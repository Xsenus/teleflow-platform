# Эксплуатация TeleFlow Platform

## 1. Ежедневный контроль

Проверять:

- `/health/ready`;
- worker heartbeat;
- delivery `waiting_review`;
- inbound `failed`;
- outbox `dead`;
- connections в pause/error;
- срок последнего backup;
- Telegram webhook last error;
- использование disk/storage;
- certificate expiry.

## 2. Локальный Windows

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
python scripts\generate_secrets.py
alembic upgrade head
.\scripts\run_dev.ps1
```

Worker:

```powershell
.venv\Scripts\Activate.ps1
python -m app.worker
```

SQLite/fake mode допустимы только для локальной разработки.

## 3. Docker

```bash
cp .env.example .env
python scripts/generate_secrets.py
docker compose up -d --build
docker compose ps
docker compose logs -f migrate api worker
```

Production:

```bash
cp deploy/.env.production.example .env
# заменить CHANGE_ME
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Host Nginx принимает HTTPS и проксирует на `127.0.0.1:8080`.

## 4. Systemd

Ожидаемые пути:

```text
/opt/teleflow-platform
/etc/teleflow-platform.env
```

Установка units:

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now teleflow-migrate.service
sudo systemctl enable --now teleflow-api.service teleflow-worker.service
```

После новой migration выполните:

```bash
sudo systemctl stop teleflow-worker teleflow-api
sudo systemctl restart teleflow-migrate
sudo systemctl start teleflow-api teleflow-worker
```

`teleflow-migrate.service` имеет `RemainAfterExit`; для повторного запуска используйте `restart`.

## 5. Doctor

```bash
python scripts/doctor.py
python scripts/doctor.py --json
```

Проверяются runtime security, DB, current/head migration, local storage write и Redis ping при включённых locks.

## 6. Worker

Один процесс выполняет:

- schedule due campaigns;
- delivery jobs;
- inbound Telegram updates;
- integration outbox;
- privacy requests;
- retention;
- heartbeat.

Команды:

```bash
python -m app.worker
python -m app.worker --once
```

Для первых MTProto-пилотов используйте один worker. Несколько worker допустимы только после PostgreSQL/Redis load test и partitioning Telegram sessions.

## 7. Monitoring

```bash
docker compose --profile observability up -d prometheus grafana
```

Основные alerts:

- API down;
- stale worker heartbeat;
- manual review jobs;
- dead outbox events;
- failed inbound updates.

Grafana по умолчанию публикуется только на `127.0.0.1`. Замените admin password и используйте VPN/SSO/reverse proxy.

## 8. Logs

Development: text. Production: JSON.

Не включать body logging на Nginx/APM. При диагностике используйте request ID, entity ID и error code. Telegram token/session/auth code, message full body и candidate contacts не должны попадать в logs.

## 9. Обновление

1. Прочитать release notes.
2. Создать encrypted backup.
3. Остановить worker.
4. Применить migration.
5. Обновить API/image.
6. Запустить `doctor`.
7. Выполнить smoke test.
8. Запустить worker.
9. Проверять alerts/queues минимум один campaign cycle.
10. Не удалять старый backup до restore validation.

Docker-порядок реализован через one-shot `migrate` dependency.

## 10. Rollback

Application rollback допустим только если backward-compatible schema подтверждена release notes. Иначе:

1. остановить API/worker;
2. сохранить текущую аварийную копию;
3. восстановить pre-upgrade DB/storage;
4. развернуть старый image;
5. выполнить doctor/smoke;
6. зафиксировать incident.

Не выполнять автоматический Alembic downgrade на production без backup и анализа потери данных.

## 11. Queue recovery

- `processing` с истёкшим lease восстанавливается worker;
- FloodWait: дождаться server time и проверить account;
- anti-spam: не пытаться обходить; manual investigation;
- `waiting_review`: проверить чат, затем решить retry/cancel/mark incident;
- write forbidden: исправить права и повторно validate destination;
- auth revoked: revoke connection и заново авторизовать;
- outbox retry: исправить endpoint, выполнить manual retry;
- outbox dead: создать incident и проверить remote idempotency.

## 12. Capacity

Перед 100 destinations измерить:

- campaign duration;
- DB job throughput;
- Telegram response/wait errors;
- worker loop latency;
- outbox backlog;
- storage and backup duration.

Hard cap не должен повышаться только ради ускорения. Индивидуальный cooldown назначения имеет приоритет.

## 13. Key rotation

### Bot token

- pause connection;
- rotate/revoke у BotFather;
- обновить через новый connection или secure update flow;
- health check;
- resume.

### MTProto session

- pause/revoke connection;
- завершить соответствующую active session в Telegram;
- пройти challenge заново.

### API key

- создать новый key;
- обновить consumer;
- проверить usage;
- revoke старый.

### Master key

1. Объявить maintenance window и остановить API/worker.
2. Создать и проверить encrypted backup БД/storage.
3. Поместить новый ключ в root-only file или environment процесса CLI.
4. Выполнить `python scripts/rotate_master_key.py --dry-run`.
5. При успешном preflight выполнить ту же команду с `--yes`.
6. Заменить `TELEFLOW_MASTER_KEY` в secret manager до старта сервисов.
7. Запустить `doctor`, audit verify, login и fake smoke.
8. Хранить старый key только в rollback escrow до restore validation.

Простая замена environment без re-encryption сделает существующие ciphertext нечитаемыми. При S3 storage учитывайте, что внешние objects и DB не образуют одну crash-atomic transaction.

## 14. Возможности 1.1

### Массовый onboarding destinations

1. Выполнить preview TXT/CSV/TSV.
2. Исправить `invalid` и проверить `duplicate`.
3. Не импортировать invite links.
4. Для каждой строки проверить evidence разрешения.
5. После apply выполнить Telegram validation.
6. Экспортировать итоговый CSV и сохранить в защищённом рабочем хранилище.

Discovery используется только для уже доступных dialogs и не заменяет permission review.

### Individual windows

При неожиданном `deferred` проверить:

- timezone назначения;
- allowed weekdays;
- окно, включая переход через полночь;
- последний successful send;
- cooldown override.

Не изменять системное время сервера ради запуска campaign.

### Visual flows

- активную revision не редактировать;
- перед активацией пройти сценарий тестовым кандидатом;
- проверить validation phone/email/age;
- проверить stop/handoff;
- новую revision привязывать к policy только после теста.

### A/B templates

- сравнивать одинаковый период и достаточный объём;
- помнить, что встроенный отчёт измеряет delivery, не hire conversion;
- при изменении campaign повторно выполнить approval;
- не использовать варианты для обхода moderation.

### ClamAV

- контролировать freshness signature database;
- при fail-closed outage восстановить scanner, а не отключать policy без incident decision;
- malware event расследовать до удаления audit/context;
- TCP port clamd держать во внутренней сети.

### PWA и browser sessions

- при потере устройства отозвать его session в разделе «Безопасность»;
- после обновления проверить новую версию service worker;
- не использовать общий browser profile для операторов;
- PWA offline page не означает, что backend доступен.


## 15. Governance operations 1.2

### Начало смены

- проверить красную global-stop полосу;
- проверить unread/critical notifications;
- проверить pending approval requests и сроки их действия;
- убедиться, что audit verify возвращает `valid=true`;
- не запускать кампанию по старому screenshot/preview после изменения данных.

### Approval workflow

1. Operator завершает draft и preview.
2. Operator/owner/admin создаёт approval request с пояснением.
3. Owner/admin сравнивает итоговый текст, route, schedule и permission evidence.
4. При four-eyes requester не принимает решение сам.
5. При high-risk campaign собирается требуемое число уникальных approvals.
6. Только после статуса `approved` доступен run-now/resume.
7. Любое изменение создаёт новый fingerprint и требует повторного workflow.

### Emergency stop

Использовать при неизвестном получателе, неожиданном дубле, FloodWait/anti-spam, подозрении на утечку credentials, ошибке маршрута или невозможности контролировать worker. Указать фактическую причину, а не формальный текст. До resume:

- остановить источник инцидента;
- проверить jobs `waiting_review`;
- проверить Telegram вручную;
- исправить permission/connection/campaign;
- получить новое утверждение, если fingerprint изменился;
- подтвердить critical notification;
- только затем выполнить resume.

### Audit verify

```bash
python scripts/audit_chain.py verify --include-system --json
```

Любой `valid=false`, missing state или hash mismatch является security incident. Не выполнять backfill поверх повреждённой цепи: сначала сохранить БД/evidence и определить причину. Backfill предназначен для legacy unchained rows после штатного обновления, а не для «исправления» обнаруженной подмены.

## 16. Controlled Operations 1.3

### Permission review

Еженедельно формируйте список разрешений, истекающих в ближайшие `TELEFLOW_PERMISSION_EXPIRY_WARNING_DAYS`. Продление выполняется только после повторной проверки правил/согласования; изменение аннулирует approval связанных кампаний.

### Preflight retention

Preflight reports сохраняются как операционные evidence. TTL ограничивает пригодность отчёта для текущего решения, но не удаляет историю. Для долгого хранения применяйте общую retention policy и backup.

### Staged capacity

Размер пакета выбирается по наблюдаемости, а не по максимальной пропускной способности. Worker concurrency не раскрывает `held`; увеличение числа worker не меняет checkpoint semantics. Перед масштабированием проверьте PostgreSQL locks и Redis singleton lock на целевом хосте.

### Reconciliation queue

`waiting_review` должен иметь нулевой backlog к концу смены. Нельзя автоматически закрывать такие jobs retention-задачей. Для каждого решения обязательны reviewer, note и при подтверждённой отправке message ID.

## 17. Production Pilot operations 1.4

### Ежедневная проверка

- открыть `/api/v1/pilot/overview` или раздел Production Pilot;
- проверить worker freshness и active connections;
- повторно валидировать только просроченные назначения контролируемыми пакетами;
- оценить permission expiry и blackout windows;
- не запускать campaign с blocked readiness;
- после изменения конфигурации формировать новый report.

### Политика TTL

Рекомендуемые исходные значения:

```env
TELEFLOW_DESTINATION_VALIDATION_TTL_HOURS=168
TELEFLOW_CONNECTION_HEALTH_TTL_HOURS=24
TELEFLOW_READINESS_TTL_MINUTES=30
```

Сокращение TTL повышает число Telegram API checks и вероятность rate limits. Увеличение TTL повышает риск работы с устаревшими правами. Значение выбирается по результатам live-пилота и правилам конкретных групп.

### Календарь

Для запланированных регламентных работ создавайте blackout заранее. После завершения разовые прошедшие окна можно удалить или оставить как историю в audit; weekly-правила нужно регулярно пересматривать при смене расписаний и часовых поясов.

## 9. Pilot stage и диагностика 1.5

Ежедневная операционная проверка дополнительно включает:

- текущий pilot stage и максимальный размер маршрута;
- срок последнего успешного live canary;
- наличие непринятых critical notifications;
- `waiting_review` и uncertain deliveries;
- свежесть последнего assessment/readiness;
- количество и TTL support bundles.

Support bundle создаётся по событию или запросу поддержки, а не по расписанию. Stage повышается только после подтверждённого предыдущего масштаба; автоматическое продвижение запрещено.

## 18. Commissioning & Portability operations 1.6

### Периодичность commissioning

Создавайте новый report после обновления приложения, миграции БД, смены storage/Redis, ротации ключей, восстановления backup, изменения public URL/TLS и перед каждым новым live-этапом формальной программы. TTL ограничивает пригодность отчёта для решения, но история сохраняется как evidence.

### Жизненный цикл configuration bundle

- создавайте архив только для конкретной операции переноса;
- передавайте его по HTTPS или через доверенный artifact registry;
- сверяйте внешний SHA-256 и внутренний manifest;
- для 1.7 проверяйте Ed25519 status/fingerprint; один неподписанный manifest не подтверждает автора;
- после import удаляйте временный файл с обеих сторон;
- контролируйте storage и audit event удаления;
- не включайте configuration bundles в бессрочный backup без отдельной бизнес-причины.

### Формальная приёмка

Pilot program является журналом технических доказательств, а не способом обойти обычные approval/readiness/safety gates. Перед sign-off оператор вручную сопоставляет campaign run с Telegram и проверяет отсутствие жалоб, удалений, ограничений и неоднозначных доставок.

## 19. Artifact Trust operations 1.7

### Ежедневная проверка

- основной signing key активен;
- основной key отмечен trusted при production policy;
- нет неожиданно отозванных ключей;
- последние configuration/support artifacts имеют `valid_trusted`;
- commissioning check `artifact_signing` не перешёл в warning/blocked;
- audit не содержит неразобранных `artifact_signing_key.*` событий.

### Создание нового ключа

1. Включить emergency stop, если операция связана с подозрением на компрометацию.
2. Создать key в разделе «Подписи и доверие».
3. Назначить default и trusted.
4. Скачать public PEM.
5. Сверить fingerprint на второй административной станции.
6. Сохранить PEM и fingerprint в защищённом операционном реестре.
7. Создать test bundle и проверить CLI.
8. Передать PEM принимающим инсталляциям по отдельному каналу.

### Плановая ротация

1. Создать новый local key, не отзывая старый.
2. Распространить новый public PEM/fingerprint.
3. Убедиться, что принимающие организации отметили новый key trusted.
4. Назначить новый key default.
5. Сформировать и проверить signed support/configuration artifacts.
6. После переходного периода отозвать старый key.

### Компрометация

1. Отозвать key — private ciphertext будет уничтожен.
2. Не удалять public record/fingerprint из incident evidence.
3. Удалить trust старого signer на других инсталляциях.
4. Создать новый key и повторить fingerprint ceremony.
5. Переформировать критичные bundles/reports.
6. Проверить audit-chain и active sessions.
7. При вероятной компрометации master key выполнить полную master-key rotation и credential rotation.

### Автономная проверка входящего файла

```bash
python scripts/verify_artifact.py incoming.zip \
  --trusted-public-key source.pem \
  --require-trusted \
  --json
```

Файл с кодом завершения 1 или 2 не импортируется. Сначала выясняется причина: unsigned, untrusted, revoked, invalid, manifest mismatch или unsupported format.

## 20. Recovery operations 1.8

Рекомендуется выполнять backup/verify из отдельного systemd timer или защищённого automation runner. Monitoring должен контролировать не exit code создания файла, а все три признака:

```text
последний backup verified
возраст ≤ RPO
последний successful drill ≤ policy age и duration ≤ RTO
```

Backup storage должен быть отделён от application storage. Age private identity и master key не должны находиться только на том же host. Для S3 включите versioning/object lock/replication и сверяйте bucket recovery независимо от database archive.

PostgreSQL metadata-only drill не закрывает production acceptance; периодически восстанавливайте dump в disposable cluster и выполняйте application smoke.

## 21. Operational SLO operations 2.2

### Ежедневный контроль

- worker heartbeat укладывается в `max_worker_heartbeat_age_seconds`;
- последняя SLO-оценка не просрочена и соответствует текущему SHA-256 policy;
- delivery error budget ниже warning threshold;
- очередь не старше установленного лимита;
- `waiting_review` разобраны вручную;
- открытые critical incidents имеют владельца и актуальный комментарий;
- Prometheus alerts `TeleFlowSLOBlocked`, `TeleFlowErrorBudgetExhausted` и `TeleFlowCriticalIncidentOpen` не остаются без подтверждения.

### Реакция на blocked assessment

1. Не ослаблять policy только ради снятия блокировки.
2. Открыть assessment и определить первый фактический blocked check.
3. Подтвердить или назначить владельца автоматического инцидента.
4. Для `worker_heartbeat` проверить процесс, БД, lock backend и журнал worker.
5. Для `queue_age` остановить новые запуски и проверить oldest due job.
6. Для `waiting_review` сверить Telegram-чат и зафиксировать результат вручную.
7. Для error budget разобрать error codes, FloodWait и revoked/write-forbidden состояния.
8. Зафиксировать mitigation/root cause.
9. Выполнить новую оценку и убедиться, что fingerprint относится к текущей policy.
10. Возобновлять staged rollout или change только после прохождения остальных gates.

### Maintenance mode

При включённой опции `suppress_incidents_during_maintenance` новые автоматические SLO-инциденты не создаются. Уже существующие SLO-инциденты разрешаются после восстановления метрики, чтобы maintenance не оставлял ложные активные блокировки. Ручные инциденты и audit события не подавляются.

## 22. Execution fencing operations 2.3

- Поддерживайте синхронизацию времени NTP/chrony.
- Не используйте одинаковый `site_key` на двух хостах.
- Не направляйте площадки в разные базы.
- Сравнивайте version и Alembic head до failover.
- Планируйте TTL так, чтобы он превышал обычный heartbeat jitter, но оставался достаточно коротким для восстановления.
- Не выполняйте автоматический failover: сначала нужно исключить in-flight и uncertain delivery.
- После каждого переключения выполняйте одну canary и независимую проверку Telegram message ID.

## Capacity operations 2.5

Capacity policy изменяется только как управляемое операционное изменение. Рекомендуемый порядок: monitor-only assessment → load dry-run → включение admission → включение dispatch → service canary → staged pilot. Любое увеличение лимитов должно иметь владельца, причину, rollback-план и post-change verification.

Worker periodically evaluates policies. Stale assessment является сигналом наблюдаемости, но непосредственный dispatch всё равно использует текущие строки базы и не доверяет старому evidence.
