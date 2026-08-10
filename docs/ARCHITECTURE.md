# Архитектура TeleFlow Platform 2.1

## 1. Контекст

TeleFlow объединяет три Telegram-сценария:

1. исходящие публикации от Bot API;
2. исходящие публикации от отдельного пользовательского MTProto-аккаунта;
3. входящие диалоги через Telegram Business connected bot.

Сценарии используют общую организацию, пользователей, storage, аудит, аналитику и observability, но имеют разные очереди и safety policies. Публикация никогда не выполняется непосредственно из HTTP-запроса.

## 2. Компоненты

```text
┌──────────────────── Browser / installed PWA shell ─────────────────────────┐
│ Login/TOTP · Publisher · Business inbox · Flows · Analytics · Security      │
│ Static shell may be cached; API/auth/webhook/PII responses are never cached │
└───────────────────────────────────┬───────────────────────────────────────────┘
                                    │ HTTPS / cookies+CSRF / API key
┌───────────────────────────────────▼───────────────────────────────────────────┐
│ FastAPI                                                                         │
│ auth · tenant scope · CRUD · preview · webhook auth · analytics · privacy       │
└────────┬──────────────────────────┬──────────────────────┬───────────────────────┘
         │                          │                      │
         ▼                          ▼                      ▼
 PostgreSQL / SQLite           Local / S3             Prometheus / APM
 state + durable queues        media/exports          metrics/traces/errors
         ▲                          ▲
         │ lease + transactions     │ optional pre-storage INSTREAM
┌────────┴──────────────────────────┴─────────────────────────────────────────────┐
│ Worker                                                                           │
│ scheduler · delivery · inbound · automation flow · outbox · privacy · retention │
└────┬─────────────────────┬────────────────────────┬──────────────────────────────┘
     │                     │                        │
     ▼                     ▼                        ▼
 Bot API/Business      MTProto/Telethon      AI / webhook / Sheets / CSV

External ClamAV daemon (optional) scans uploaded bytes before storage.
Redis provides distributed singleton locks for scheduler/retention.
Nginx terminates TLS, applies limits and denies external /metrics.
```

## 3. Слои кода

```text
app/
  api/                         HTTP controllers, DTO mapping, RBAC
    analytics.py              aggregate analytics and anonymized CSV
    auth.py                   browser sessions and device revocation
    automation.py             flows, policies, providers and knowledge
    destinations.py           CRUD, import/export and dialog discovery
    commissioning.py          persisted environment checks
    pilot_programs.py         formal staged acceptance and evidence
    configuration_bundles.py safe non-secret portability API
  services/
    ai/                        providers and AI orchestration
    telegram/                  Bot API, Business, MTProto and fake gateways
    antivirus.py              ClamAV INSTREAM adapter
    campaign_variants.py      stable A/B assignment
    destination_bulk.py       safe TXT/CSV/TSV parser and planner
    destination_windows.py    timezone/window/cooldown calculations
    flows.py                   graph validation and deterministic execution
    commissioning.py          DB/storage/worker/audit readiness probes
    pilot_programs.py         immutable stage evidence and acceptance report
    config_bundles.py         manifest-validated export/import
  models.py                    SQLAlchemy persistence model
  schemas.py                   Pydantic input/output contracts
  security.py                  Argon2/JWT/cookies helpers
  middleware.py                request ID, headers, IP allowlist, CSRF
  observability.py             metrics, Sentry and OpenTelemetry
  static/                      dependency-free SPA and PWA shell
migrations/                    Alembic revisions
scripts/                       QA, doctor, backup/restore and smoke
```

API controllers никогда не возвращают зашифрованные credentials. Worker не принимает tenant из непроверенных входных данных: `organization_id` берётся из связанной DB-сущности.

## 4. Multi-tenant модель

`Organization` является корнем данных. Organization scope применяется к:

- users, refresh sessions и API keys;
- Telegram connections/destinations;
- templates/media/campaigns/jobs;
- Business connections/inbound updates;
- conversations/messages/candidates/flows;
- AI providers/knowledge/interactions;
- integrations/outbox/privacy/audit.

Tenant isolation применяется в трёх местах:

1. HTTP lookup содержит `id AND organization_id`;
2. worker выбирает связанные объекты в scope организации;
3. limits, analytics, storage paths и audit считаются внутри организации.

Прямой UUID объекта другого tenant не должен отличаться от несуществующего объекта.

## 5. Browser auth и активные сессии

```text
Login → short access JWT + random refresh token + CSRF cookie
                                  │
                                  ▼
                         RefreshSession row
                    SHA-256 token hash · expiry · device metadata
```

При refresh старый token отзывается и заменяется новым. Повторное использование старого token считается replay и завершает token family. Пользователь может:

- просмотреть собственные активные sessions;
- отозвать конкретное устройство;
- завершить все остальные sessions;
- отозвать текущую session и немедленно выйти.

В БД не хранится raw refresh token.

## 6. PWA

Service worker кэширует только версионированный статический shell:

- HTML shell;
- CSS/JavaScript;
- manifest;
- icons;
- отдельную offline page.

Запросы к `/api`, `/hooks`, `/metrics`, auth endpoints и любые non-GET requests всегда идут в сеть и никогда не записываются в Cache Storage. Offline page не содержит пользовательских данных. PWA не заменяет TLS, backend или browser session.

## 7. Исходящая публикация

```text
Template A [+ optional Template B] + Media + ordered Destinations
                         │
                         ▼
                    Campaign draft
                         │ preview
                         ▼
 blockers/warnings + destination window + stable variant assignment
                         │ explicit approval request
                         ▼
 decisions + current SHA-256 fingerprint + optional four-eyes
                         │ run-now / schedule
                         ▼
             CampaignRun + immutable DeliveryJob snapshots
                         │
                         ▼
              Safety Engine immediately before send
                         │
                  Telegram gateway
                         │
                         ▼
          message_id / retry / waiting_review / failure
```

### Snapshot и A/B assignment

`CampaignVariantService` вычисляет стабильный SHA-256 bucket из `campaign_id` и `destination_id`. Если bucket попадает в вес B, destination получает secondary template. Повторные runs сохраняют вариант для той же пары.

Job хранит:

- template ID и variant A/B;
- body и parse mode;
- media reference;
- link preview setting;
- destination/topic;
- idempotency key.

Редактирование template или campaign после approval не меняет уже созданные jobs. Изменение draft/paused campaign снимает прежнее approval.

### Lease

Worker атомарно переводит due job в `processing`, назначает `lease_until` и `locked_by`. При падении процесса просроченный lease восстанавливается. PostgreSQL использует `FOR UPDATE SKIP LOCKED`; SQLite поддерживается как локальный однопроцессный режим.

## 8. Destination onboarding и окна публикации

### Bulk import

Parser принимает TXT/CSV/TSV, нормализует username/public link/chat ID и формирует план без немедленной записи. Invite links отклоняются, потому что import не должен вступать в группы.

Каждая строка получает статус:

- ready;
- duplicate;
- invalid;
- deferred.

Apply повторно проверяет ограничения БД и безопасно обрабатывает конкурентный duplicate через savepoint.

### Dialog discovery

Gateway возвращает dialogs, доступные уже авторизованному connection. UI позволяет выбрать их, но перед созданием каждый dialog повторно разрешается gateway. Обнаружение не является доказательством разрешения на публикацию: permission evidence заполняется отдельно.

### Per-destination window

`DestinationWindowService` работает в IANA timezone назначения и рассчитывает:

- разрешён ли текущий weekday;
- попадает ли local time в окно;
- поддерживается ли окно через полночь;
- ближайшее разрешённое UTC время;
- окончание персонального cooldown.

Safety Engine может перенести `due_at` без сетевого вызова.

## 9. Safety Engine

Safety Engine вызывается и при preview, и непосредственно перед send. Проверяются:

- organization emergency-stop, connection and campaign status;
- current approval request and SHA-256 fingerprint;
- permission evidence;
- destination validation state;
- individual days/time window;
- destination cooldown;
- minimum interval;
- connection/organization/global daily caps;
- FloodWait/manual review state;
- media object availability.

Gateway преобразует transport-specific errors в domain errors:

| Событие | Стратегия |
|---|---|
| SlowMode | defer только текущего destination |
| Telegram FloodWait | pause connection и сохранить server wait |
| PeerFlood/anti-spam | manual review без autoretry |
| Write forbidden | отключить destination |
| Session revoked/auth | connection error/reconnect |
| Timeout после возможной отправки | `waiting_review`; автоматический повтор запрещён |
| Явная временная ошибка до send | bounded retry |

## 10. Governance control plane

### Approval request and fingerprint

`ApprovalService` отделяет подготовку кампании от права запуска. Запрос утверждения хранит fingerprint, policy snapshot, автора, TTL и решения. Для high-risk route число решений повышается по policy организации. При four-eyes автор запроса исключается из списка допустимых approvers.

Fingerprint строится из канонического JSON и охватывает connection safety settings, templates/revisions/body hashes/media, расписание, порядок назначений, permission evidence, timezone/windows/cooldown. Mutation campaign/destination invalidates active approval. Safety Engine пересчитывает fingerprint перед каждым gateway call.

```text
operator edits draft
        │
        ▼
submit approval ── fingerprint F1 ── pending request
        │                              │
        │                        owner/admin decisions
        │                              │
        └── any protected change ──────┴─> cancelled/stale
                                       │
                                enough approvals
                                       │
                                  approved F1
```

### Organization emergency stop

`Organization.publishing_paused` является tenant-level kill switch. Pause endpoint блокирует organization row, фиксирует reason/actor/time, переводит due jobs в `waiting_review` и создаёт audit/notification/outbox events. Scheduler не создаёт новые runs, а Safety Engine повторно проверяет флаг после аренды job и до сети. Поэтому уже взятый worker не может обойти остановку.

Resume очищает stop state и возвращает только удержанные jobs в очередь. Approval fingerprint, destination permission, Telegram connection state, caps и windows проверяются заново.

### Notifications

`NotificationService` создаёт tenant-scoped info/warning/critical events и коалесцирует повторяющиеся события по dedup key/window. UI поддерживает unread/read/acknowledged. Создание notification публикует domain event в durable outbox, поэтому внешний incident channel может подписаться без прямого сетевого вызова из delivery transaction.

### Audit hash-chain

`write_audit` сериализует запись через `AuditChainState` organization/system scope:

```text
entry_hash = SHA256(canonical(sequence, prev_hash, actor, action,
                              entity, redacted_details, request metadata,
                              created_at, chain_version))
```

Каждая запись хранит `sequence`, `prev_hash`, `entry_hash`, `chain_version`; state хранит head. Verify API/CLI пересчитывает цепь и обнаруживает подмену записи, пропуск последовательности и удаление state. Для legacy rows используется offline backfill. Это tamper-evident control, а не замена внешнему immutable log sink.

### Master-key rotation

`KeyRotationService` сначала расшифровывает все поддерживаемые encrypted values старым key и только затем начинает re-encryption. Dry-run не меняет БД/storage. Реальная операция требует остановки сервисов и backup; DB transaction защищает database fields, а encrypted privacy objects во внешнем storage получают best-effort rollback journal.

## 11. Telegram gateways

Общий контракт включает identity, destination resolution/discovery и send.

### BotApiGateway

- HTTPS Bot API;
- token расшифровывается только на время запроса;
- text/photo/document;
- parse mode, link preview и forum topic;
- health/permission checks.

### MTProtoGateway

- Telethon;
- encrypted `api_hash` и StringSession;
- challenge start/complete;
- dialogs discovery;
- отдельная safety policy для user account.

### FakeGateway

- не обращается к сети;
- возвращает deterministic `fake-*` message ID;
- используется в automated tests и local smoke.

## 12. Media и antivirus

До storage выполняются:

1. size check;
2. allowlisted MIME check;
3. signature/magic-byte validation;
4. UTF-8 validation для текстовых formats;
5. optional ClamAV scan;
6. SHA-256 calculation;
7. write в local/S3 storage.

ClamAV adapter использует TCP `INSTREAM`: файл не передаётся в shell и не исполняется. При malware result upload отклоняется и создаётся critical audit event. При scanner error behavior определяется `antivirus_fail_closed`.

## 13. Telegram Business inbound

```text
Telegram
   │ POST /hooks/telegram/{connection}/{path_token}
   ▼
Path/header authentication → body limit → JSON validation
   ▼
Encrypted InboundTelegramUpdate + redacted preview + commit
   ▼ immediate 200
Inbound worker
   ├─ business connection state
   ├─ conversation/message upsert
   ├─ consent/stop/handoff policy
   ├─ deterministic flow or AI service
   ├─ candidate update
   ├─ Business reply
   └─ outbox event
```

Webhook не ждёт AI или Telegram reply. Unique `(telegram_connection_id, telegram_update_id)` делает приём идемпотентным. Raw payload хранится зашифрованно и удаляется по короткому retention.

## 14. Visual automation flows

Flow definition является валидируемым graph JSON. Validator проверяет:

- уникальные node IDs;
- допустимые node types;
- существование переходов;
- достижимость;
- отсутствие циклов;
- terminal node;
- корректность choice aliases;
- совместимость field/validation.

Web UI редактирует линейный граф через карточки шагов, drag-and-drop и кнопки изменения порядка. Активная revision неизменяема. Для изменения создаётся новая revision.

Execution хранит текущий node в conversation state. Question/choice валидируют ответ, обновляют candidate и переходят к следующему node. Phone/email передаются только в encrypted candidate fields.

## 15. Диалоги и AI

Full body сообщения зашифрован с AAD `conversation/message`; list API использует redacted preview. AI service получает только ограниченную историю текущего диалога, текущую candidate card и активные knowledge articles своей организации.

Provider contract:

```json
{
  "reply_text": "...",
  "candidate_updates": {},
  "vacancy_key": null,
  "handoff": false,
  "safety_flags": {}
}
```

Rule-based provider работает офлайн. OpenAI-compatible provider применяет model allowlist, strict JSON response, timeout и context limits. `AIInteraction` сохраняет request hash, safe previews, latency/tokens/status, но не API key.

## 16. Analytics

Analytics API выполняет tenant-scoped aggregation за заданный UTC range с отображением timezone организации. Оно не читает decrypted message bodies или candidate contacts.

Агрегаты:

- delivery statuses и success rate;
- daily timeseries;
- conversations, handoff и consent;
- candidates funnel;
- AI interactions/latency;
- integration outbox;
- campaigns;
- A/B template delivery.

CSV export содержит только агрегаты и IDs/names разрешённых бизнес-сущностей.

## 17. Outbox и интеграции

Domain event создаётся в той же DB-транзакции, что и бизнес-изменение. Outbox содержит snapshot target endpoints. `delivery_state` хранит статус каждого получателя, поэтому успешный endpoint не вызывается повторно после сбоя другого.

Adapters:

- webhook: JSON + `X-TeleFlow-Signature: sha256=...`;
- Google Sheets: service account OAuth и append;
- CSV: отдельный immutable object на event.

Outbox имеет lease, bounded retries и `dead` status.

## 18. Privacy

Privacy request разрешает subject по conversation/chat/user. Export формируется во временном encrypted object и имеет TTL. Delete выполняет scrubbing identifiers, bodies и contacts, сохраняя минимальный технический audit.

Retention worker удаляет:

- raw inbound payload после короткого периода;
- PII после organization retention;
- просроченные export files.

## 19. Storage

`StorageService` предоставляет `put/read/delete/exists/materialize`.

- Local backend нормализует key и запрещает traversal.
- S3 backend использует private bucket/prefix.
- Temporary materialization применяется только там, где adapter требует path.
- Media metadata содержит size, MIME и SHA-256.

## 20. Observability

API экспортирует внутренний `/metrics`:

- build info;
- HTTP requests/latency;
- queue depth delivery/inbound/outbox;
- worker heartbeat age;
- safety/delivery counters.

Внешний Nginx возвращает 404 для `/metrics`; Prometheus работает во внутренней сети. Sentry и OTLP опциональны.

## 21. Deployment topology

### Локально

SQLite + API + один worker + fake mode.

### Production

```text
Internet → host Nginx TLS → compose Nginx → API
                                  │
        PostgreSQL + Redis + Worker + Local/S3 + optional clamd
                                  │
                    Prometheus/Grafana (internal)
```

`migrate` завершается до API/worker. Production запрещает `create_all`.

## 22. Масштабирование

- API stateless после S3/external DB;
- API replicas допустимы за load balancer;
- scheduler/retention используют Redis singleton locks;
- delivery/outbox/inbound используют DB leases;
- одна MTProto session не должна выполняться независимыми processes одновременно;
- увеличение workers требует partitioning connections и отдельного live-теста;
- A/B assignment не зависит от количества workers.

## 23. Controlled Operations 1.3

Контур 1.3 добавляет три независимых барьера поверх approval и Safety Engine.

### Persisted preflight

`CampaignPreflightReport` является tenant-scoped immutable snapshot локально проверяемого состояния. Он содержит campaign fingerprint, blockers/warnings, per-destination content fingerprint, batch number, ожидаемое время, срок разрешения и ссылку на найденный дубликат. Отчёт approval-time служит оператору, но scheduler создаёт отдельный свежий отчёт непосредственно перед run.

### Staged queue

`CampaignRun` фиксирует policy пакетов. Первый пакет получает `pending`, остальные — `held`. SQL lease worker выбирает только `pending/retry`; поэтому UI, scheduler или restart не могут случайно отправить удержанный пакет. `release_next_batch` является единственным переходом `held → pending`, а checkpoint/abort выполняются под row lock.

### Reconciliation

Неоднозначный transport result создаёт `waiting_review / DELIVERY_RESULT_UNCERTAIN`. Решение хранится непосредственно на `DeliveryJob` вместе с reviewer/time/note. `confirmed_not_sent` не является обычным retry: API повторно проверяет connection, permission, campaign и approval. `confirmed_sent` обновляет cooldown так же, как штатный success.

### Duplicate guard

Content fingerprint вычисляется из нормализованного body, parse mode, media SHA-256 и link-preview policy. Guard tenant- и destination-scoped; он выполняется в preflight и повторно в Safety Engine перед network call.

## 24. Production Pilot 1.4

### Validation ledger

`Destination` хранит текущее агрегированное состояние проверки, а `DestinationValidationRecord` — неизменяемую историю попыток. Это разделяет быстрый Safety lookup и операционную доказательность.

```text
Destination
  validated / validated_at / validation_expires_at
        ↑
resolve_destination through Bot API or MTProto
        ↓
DestinationValidationRecord
  status / capabilities / error / source / checked_by / checked_at
```

Bulk-validation выполняется последовательно. После gateway-ошибки с `retry_after` новые destination-запросы не выполняются, а оставшиеся строки получают `deferred`.

### Blackout policy calendar

`PublishingBlackout` имеет tenant scope, целевой scope и один из двух видов временного правила. `evaluate_blackouts` принимает UTC-время, переводит weekly-правила в локальную IANA timezone и возвращает все активные совпадения и общий `defer_until`.

Порядок в delivery path:

```text
lease job
→ organization emergency switch
→ campaign/approval/permission/validation checks
→ blackout evaluation
→ destination window/cooldown/caps/duplicate checks
→ build Telegram gateway
→ send
```

Таким образом, blackout не создаёт сетевой запрос и не считается попыткой отправки.

### Readiness report

`PilotReadinessReport` является короткоживущим снимком операционной готовности. Он содержит:

- campaign/connection/organization references;
- campaign fingerprint;
- structured checks;
- blockers/warnings;
- linked fresh preflight report;
- author and expiry.

API создаёт отчёт синхронно по явному действию оператора. `run-now`, `resume` и scheduler используют только report с допустимым статусом, неистёкшим TTL и совпадающим fingerprint. Изменение кампании автоматически делает отчёт неактуальным без мутации истории.

### Web control plane

SPA route `pilot` агрегирует `/pilot/overview`, readiness reports, destinations and blackouts. UI не является security boundary: все изменения повторно проходят RBAC, CSRF, tenant lookup и schema validation на backend.

## 25. Pilot Certification & Diagnostics 1.5

### Серверный этап масштаба

`Organization.pilot_stage` задаёт не рекомендацию интерфейса, а верхнюю границу числа включённых назначений в одном live-запуске. Порядок этапов фиксирован:

```text
LOCAL → SERVICE → FIVE → TWENTY → FIFTY → HUNDRED
```

Gate применяется в preview, run-now, resume, scheduler, staged checkpoint и Safety Engine. Последняя проверка выполняется до создания Telegram gateway, поэтому уже созданное задание также не может обойти пониженный этап.

### Оценка следующего этапа

`PilotStageAssessment` является короткоживущим immutable-снимком. Сервис сохраняет checks, blockers, warnings, fingerprint, автора и TTL. Повышение разрешено только на следующий последовательный этап и только когда assessment остаётся актуальным. Fingerprint включает состояние организации, worker, Telegram-подключений, разрешённых назначений, canary и фактического run evidence.

```text
оператор создаёт assessment
        ↓
backend проверяет live prerequisites
        ↓
Owner/Admin вводит точную confirm-фразу
        ↓
assessment повторно проверяется по TTL/fingerprint
        ↓
organization.pilot_stage обновляется
```

Понижение этапа является fail-safe операцией: несовместимые активные кампании и ожидающие задания приостанавливаются, а не продолжают старый маршрут.

### One-shot canary

`PilotCanaryAttempt` хранит только marker, SHA-256 фиксированного текста, outcome и transport metadata. Произвольного рекламного текста в canary API нет. Перед единственным сетевым вызовом connection и destination блокируются транзакционно, повторно проверяются permissions, blackout, window, cooldown, caps и Telegram health. Autoretry отсутствует.

Fake canary помечается отдельно и не входит в live evidence.

### Support bundle

`SupportBundle` указывает на краткоживущий ZIP в storage. Генератор строит отдельные redacted DTO и никогда не сериализует ORM-объекты целиком. Идентификаторы псевдонимизируются, тексты, PII, chat IDs, usernames, rules URLs и credentials исключаются. Архив получает внутренний `MANIFEST.sha256`, внешний SHA-256 и TTL. Download выполняет hash verification и возвращает `Cache-Control: no-store`.

Retention worker удаляет истёкшие файлы и переводит запись в terminal status. Support bundle не является резервной копией и не должен использоваться для восстановления данных.
## 14. Commissioning и перенос 1.6

Commissioning report является отдельным persisted artifact и не заменяет campaign readiness или delivery-time Safety Engine. Formal pilot program ссылается на campaign, commissioning report, readiness/preflight и конкретный campaign run. Evidence snapshot сохраняет только технические результаты и identifiers, а не тексты сообщений или credentials.

Configuration bundle строится из tenant-scoped read models. Secret columns не читаются в export document. Import выполняется в одной DB-транзакции, а создаваемые сущности принудительно переводятся в draft/disabled/unverified. Медиа сначала проверяется по manifest и SHA-256, затем проходит обычный media validation pipeline.

## 26. Artifact Trust architecture 1.7

### Key registry

`ArtifactSigningKey` является tenant-scoped registry публичных ключей и, опционально, локальной зашифрованной приватной части.

```text
Organization
    └─ ArtifactSigningKey
         ├─ public_key_b64 / fingerprint / key_id
         ├─ private_key_enc (optional, AES-GCM)
         ├─ trusted_for_import
         ├─ is_default
         └─ active / revoked
```

Локально сгенерированный ключ может подписывать. Импортированный публичный ключ может только подтверждать происхождение. Default-key transition блокирует organization row, чтобы два параллельных запроса не назначили разные основные ключи.

### Signing pipeline

```text
artifact payload
    ↓ canonical payload or exact manifest bytes
SHA-256
    ↓
signature metadata + purpose
    ↓ domain separation
Ed25519 private key
    ↓
SIGNATURE.json
```

Configuration bundle и support bundle используют manifest как корень Merkle-подобной схемы: manifest содержит digest каждого payload-файла, а Ed25519 защищает manifest. Acceptance report подписывает canonical JSON payload напрямую.

### Verification pipeline

```text
untrusted bytes
    ↓
format detection: configuration ZIP / support ZIP / acceptance JSON
    ↓
ZIP/path/schema/composition limits
    ↓
per-file SHA-256 or canonical payload SHA-256
    ↓
strict purpose + signature metadata validation
    ↓
Ed25519 verification
    ↓
tenant-local trust/revoke lookup
    ↓
policy: optional / require_valid / require_trusted
```

Pure verifier не требует базы и подходит CLI. API добавляет tenant-local knowledge: known signer, trusted signer и revoked signer. Import service использует DB-aware verifier и применяет policy до любых изменений tenant configuration.

### Secret boundary

Private raw key появляется в памяти только на короткое время при генерации или подписи. В БД сохраняется AES-GCM ciphertext с AAD `artifact-signing-key:<id>:private`. API schemas и frontend получают только public metadata. Revoke уничтожает ciphertext, оставляя public record для проверки истории.

### Operational integration

- commissioning добавляет проверку default/trusted signing key;
- configuration/support rows сохраняют signature status/info/fingerprint;
- acceptance endpoint подписывает отчёт в момент download;
- master-key rotation включает private signing keys;
- release validator проверяет migration, CLI, документацию, production policy и отсутствие private field во frontend;
- offline verifier позволяет проверять файл до запуска или импорта на другой инсталляции.

## 27. Recovery Assurance architecture 1.8

```text
CLI backup host
  ├─ consistent DB snapshot / pg_dump
  ├─ optional local storage snapshot
  ├─ MANIFEST.json + Ed25519 SIGNATURE.json
  ├─ ZIP → optional age encryption
  └─ signed backup receipt
           │
           ├─ web import: receipt metadata only → registered
           ├─ CLI verify: archive/manifest/files/database → verified
           └─ CLI drill: isolated checks → signed drill receipt

FastAPI / PostgreSQL
  ├─ recovery_policies
  ├─ recovery_backup_evidence
  └─ recovery_restore_drills
           │
           └─ commissioning recovery_assurance gate
```

Raw backup и age identity не проходят через FastAPI. Разделение control plane и recovery data plane исключает browser-triggered destructive restore и уменьшает площадь утечки. RPO вычисляется только по `verified` evidence; receipt import сам по себе не повышает доверие.

SQLite drill использует отдельную восстановленную copy. PostgreSQL 1.8 фиксирует `metadata_only`, пока dump не восстановлен во внешнюю disposable DB.

## Supply Chain Assurance architecture 2.1

```text
ReleaseAttestation 2.0
  ├─ deterministic CycloneDX 1.6 SBOM
  ├─ signed transparency event chain
  └─ external/inventory dependency report
          ↓ normalize + bind to SBOM/attestation
ReleaseDependencyAssessment
  ├─ report payload + SHA-256
  ├─ signature envelope + signer fingerprint
  ├─ SBOM snapshot + SHA-256
  ├─ dependency policy snapshot + SHA-256
  ├─ aggregate findings + expiry
  └─ immutable evidence ID
          ↓ exact FK
ChangeRequest
  └─ pre/post verification
       ├─ trusted release attestation
       ├─ published transparency state + valid chain
       └─ exact current dependency assessment
```

Transparency sequence allocation serializes on the organization row. Dependency verification deliberately separates immutable evidence from derived status: report/SBOM/policy hashes remain unchanged, while signature state, counts and blockers may be recomputed. The server environment sets a hard upper bound for report TTL.

## Operational SLO & Incident Assurance 2.2

```text
DeliveryJob / WorkerHeartbeat / Incident
                 │
                 ▼
          SLO evaluation service
                 │ immutable policy snapshot + metrics + fingerprint
                 ▼
           SLOAssessment
              │      │
              │      └── automatic deduplicated Incident + IncidentEvent
              ▼
       unified SLO gate decision
        │          │          │
        ▼          ▼          ▼
 campaign API   scheduler   Safety Engine
                            before Telegram gateway
```

`SLOAssessment` не редактируется. Изменение `SLOPolicy` меняет `policy_sha256` и делает предыдущую оценку неактуальной. Scheduler и Safety Engine не доверяют только UI: они повторно вызывают один service-level gate.

Инцидент и его события разделены: текущий агрегат хранит состояние, а `IncidentEvent` — неизменяемую хронологию переходов. Все переходы дополнительно фиксируются в общей audit hash-chain.

## Execution Fencing & Failover Assurance 2.3

### Новые компоненты

```text
ExecutionSite
  heartbeat и identity площадки

ExecutionLease
  active site + holder worker + monotonic epoch

FailoverRequest
  двухэтапное controlled switching

DeliveryAttempt
  durable evidence каждой сетевой попытки
```

### Путь Telegram-доставки

```text
worker выбирает due job
  → claim tenant execution lease
  → stamp site/epoch в job
  → Safety Engine проверяет execution gate
  → commit DeliveryAttempt(prepared)
  → SELECT FOR UPDATE execution lease
  → commit network_started отдельной транзакцией
  → Telegram gateway: ровно один вызов
  → sent/failed/uncertain
```

Execution row lock удерживается основной delivery-транзакцией до фиксации результата. Failover не может увеличить epoch в середине вызова. Отдельный durable marker позволяет stale-recovery отличить падение до сети от падения после начала сети.

### Scheduler

Scheduler вызывает `scheduler_site_allowed` до создания `CampaignRun` и повторно перед созданием jobs. Standby-площадка не генерирует сетевую работу даже при одинаковом расписании и общей базе.

### Failover

```text
request
  → execution lease DRAINING
  → blocker calculation
  → independent approval
  → epoch + 1
  → target ACTIVE
```

Processing jobs и unresolved attempts блокируют switch. Все решения tenant-scoped и записываются в audit-chain.

## Continuity Assurance 2.4

Continuity использует существующий execution lease как единственный authoritative источник active-площадки. Отдельного обходного механизма переключения нет.

```text
ExecutionSite heartbeat
        ↓ runtime evidence
ExecutionLease + epoch
        ↓
ContinuityDrill
        ↓ controlled FailoverRequest
Standby active
        ↓ controlled FailbackRequest
Primary restored
        ↓
Evidence + hash-linked events + independent sign-off
```

`continuity_policies` задаёт требования организации. `continuity_drills` хранит state machine и immutable evidence snapshot. `continuity_drill_events` хранит последовательную SHA-256 chain. Worker только проецирует уже завершённые execution requests и не инициирует переключение.

Runtime evidence строится в `app/services/runtime_evidence.py` из несекретных параметров. Telegram delivery по-прежнему проходит `ExecutionCoordinator`, `SafetyEngine` и транзакционный fence.

## Capacity & Backpressure Assurance 2.5

### Компоненты

```text
CapacityPolicy
    ↓
CapacitySnapshot ← DeliveryJob / CampaignRun / DeliveryAttempt / TelegramConnection
    ↓
CapacityAssessment
    ↓
Admission / Ready Release / Dispatch Decision
```

`app/services/capacity.py` является единственным доменным источником правил. API, scheduler, rollouts, Safety Engine, delivery worker и manual retry вызывают этот сервис, а не воспроизводят формулы самостоятельно.

### Admission sequence

```text
run-now/resume/scheduler/retry
→ projected jobs/runs
→ capacity_admission_decision
→ blocker: no mutation
→ allowed: transaction
→ lock policy and re-evaluate
→ create run/jobs
```

Повторная проверка внутри scheduler-транзакции закрывает TOCTOU между пользовательским запросом и фактическим созданием очереди.

### Dispatch sequence

```text
Safety Engine precheck
→ job processing
→ DeliveryAttempt(prepared)
→ commit durable reservation
→ lock CapacityPolicy
→ count actual starts + prepared reservations
→ blocked: abandon attempt and return job to pending
→ allowed: build Telegram gateway
→ network_started marker
→ Telegram call
```

Блокировка policy освобождается до сетевого запроса. Execution Fencing отдельно гарантирует, что право на отправку принадлежит текущей площадке и epoch.

### Источник истины

CapacityAssessment — сохраняемое evidence для наблюдения и аудита. Источником разрешения на Telegram-вызов остаётся свежая повторная проверка текущих строк базы данных, а не старый assessment или состояние SPA.
