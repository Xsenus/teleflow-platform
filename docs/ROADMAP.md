# TeleFlow Platform — полный roadmap

## Release 2.5 — Capacity & Backpressure Assurance

- [x] Tenant-scoped ordered capacity policy.
- [x] Immutable assessments с SHA-256, TTL, blockers и warnings.
- [x] Admission control до создания run/jobs и повторно внутри транзакции.
- [x] Ready-budget для staged rollout, checkpoint и manual retry.
- [x] Minute/hour network-start budgets.
- [x] Safety Engine precheck до Telegram gateway.
- [x] Durable dispatch reservation против concurrent workers.
- [x] Estimated drain time и utilization по самому насыщенному измерению.
- [x] Worker assessment, Prometheus, alerts и Grafana.
- [x] 32-й SPA-раздел «Нагрузка и лимиты».
- [x] Production-required capacity gate.
- [x] Docstring/JSDoc gate для всех именованных функций.
- [x] Alembic `bd5f7a9c3e6f` и round-trip 2.4↔2.5.

**Definition of Done:** полный автоматический QA и повторная проверка распакованного ZIP; фактические throughput thresholds и live Telegram acceptance требуют инфраструктуру владельца.


Версия документа: **2.5**
Дата фиксации: **9 августа 2026 года**
Статус: **кодовый roadmap 2.5 реализуется; production load-test и Telegram-пилот остаются внешней приёмкой владельца**

## 1. Цель и границы

TeleFlow — self-hosted платформа, которая покрывает исходный запрос полностью:

- публикация от обычного Telegram-бота;
- публикация от отдельного пользовательского Telegram-аккаунта;
- управляемый список разрешённых групп, каналов и тем;
- веб-интерфейс, backend, расписание, очередь и аудит;
- запуск на ПК и перенос на сервер;
- безопасная остановка при ограничениях Telegram;
- Telegram Business для входящих обращений;
- визуальная анкета кандидата, AI-помощник и handoff оператору;
- интеграции, аналитика, privacy и production security.

TeleFlow **не реализует** обход антиспама, подбор «антибан-интервалов», ротацию аккаунтов/прокси, автоматическое вступление, массовые личные сообщения, маскировку автоматизации и публикации без подтверждённого разрешения.

## 2. Итоговая матрица 2.5

| Направление | Результат | Статус |
|---|---|---|
| Web UI | 32 адаптивных русскоязычных раздела без CDN | Готово |
| PWA | Устанавливаемый shell, безопасная offline-страница, no-cache для API/PII | Готово |
| Backend | FastAPI, OpenAPI, Pydantic, RBAC, request audit | Готово |
| Persistence | SQLAlchemy 2, Alembic, PostgreSQL, SQLite dev/test | Готово |
| Multi-tenant | Organization scope в API, worker, limits, storage и audit | Готово |
| Bot API | Identity, health, destination resolution, text/media/topic delivery | Готово; нужен live token pilot |
| MTProto | Phone/code/2FA challenge, encrypted StringSession, send/discovery | Готово; нужен live credential pilot |
| Destinations | Permission evidence, validation history/TTL, review/expiry, bulk import/export/discovery | Готово |
| Destination windows | IANA timezone, weekdays, time window, per-chat cooldown | Готово |
| Media | Signature/MIME/size checks, local/S3, optional ClamAV | Готово |
| Campaigns | Edit/copy, preview, explicit approval, schedules, A/B, preflight и staged rollout | Готово |
| Governance | Four-eyes, multi-approval, TTL, fingerprint и policy checks | Готово |
| Emergency stop | Organization-wide stop до Telegram network call | Готово |
| Notifications | Tenant center, deduplication, read/ack и outbox | Готово |
| Durable delivery | Jobs, leases, held batches, retries, stale recovery и reconciliation | Готово |
| Safety Engine | Caps, intervals, permission/validation expiry, blackout calendar, duplicate guard и Telegram restrictions | Готово |
| Telegram Business | Authenticated webhook, deduplication, inbound queue | Готово; нужен live profile pilot |
| Conversations | Consent, stop, handoff, operator reply, encrypted bodies | Готово |
| Visual flows | Versioned deterministic graph, validation, drag-and-drop | Готово |
| Candidates | Structured profile, encrypted contacts, controlled export | Готово |
| AI | Offline rule-based + OpenAI-compatible, KB, safety contract | Готово; внешний provider требует ключ |
| Analytics | Delivery, dialogs, funnel, AI, outbox, A/B delivery, CSV | Готово |
| Integrations | HMAC webhook, Google Sheets, CSV, per-target durable outbox | Готово |
| Service API | Scoped/expiring/revocable API keys | Готово |
| Privacy | Export/delete, TTL, retention, redaction and scrubbing | Готово |
| Security | Argon2id, TOTP, JWT rotation, sessions, AES-GCM, CSP, key rotation | Готово |
| Audit integrity | Sequential SHA-256 hash-chain, verify и offline backfill | Готово |
| Observability | Prometheus, alerts, Grafana, Sentry, OpenTelemetry | Готово |
| Deployment | Docker, Nginx, systemd, migrations, doctor | Готово |
| Backup | Signed receipt/manifest, PostgreSQL/SQLite/storage, age encryption и safe verification | Готово |
| Pilot certification | Stage limits, live canary, assessments, real-run evidence | Готово; live evidence требует Telegram credentials |
| Commissioning | Persisted DB/storage/worker/security checks with TTL/fingerprint | Готово |
| Formal pilot acceptance | Local/live stages, immutable run evidence, independent sign-off, acceptance report | Готово; live evidence требует Telegram credentials |
| Safe portability | Non-secret configuration ZIP, preview/import, fail-safe state, audited deletion | Готово |
| Safe diagnostics | Redacted support bundle, SHA-256, TTL и no-store download | Готово |
| Artifact trust | Ed25519 signer registry, trusted import, offline verifier и signed evidence | Готово |
| Recovery assurance | RPO/RTO, registered→verified, signed backup/drill evidence и isolated restore | Готово; production PostgreSQL drill требует инфраструктуру |
| Change management | Independent approval, maintenance mode, pre/post deploy verification и rollback evidence | Готово |
| Release trust | Ed25519 provenance, dependency inventory, target-version binding и production upgrade gate | Готово |
| Supply-chain assurance | Signed transparency log, CycloneDX SBOM, dependency policy/assessment и upgrade binding | Готово; реальный scanner feed остаётся внешним gate |
| Operational SLO | Delivery success, error budget, immutable assessments и gates публикаций/изменений | Готово |
| Execution fencing | Active/standby sites, tenant lease, monotonic epoch, durable attempts и controlled failover | Готово; межхостовый live-drill требует общую PostgreSQL и две площадки |
| Continuity assurance | Simulation без сети, live failover/failback, runtime compatibility, RTO, event-chain и independent sign-off | Готово; фактический двуххостовый drill требует инфраструктуру владельца |
| Capacity assurance | Admission, ready-release, dispatch reservations, rate budgets, drain forecast и immutable assessments | Готово; production thresholds требуют load-test владельца |
| Incident assurance | Lifecycle, immutable event history, automatic SLO incidents и audit | Готово |
| QA | 536 тестов, coverage 90,71% при gate 80%, русская documentation gate, Linux/Windows CI, migration/HTTP/worker/audit/recovery/capacity/continuity gates | Готово |

## 3. Этап 0 — анализ и безопасные границы

### Реализовано

- Bot API, MTProto и Business automation разделены архитектурно;
- гарантированный «безопасный интервал» не обещается;
- публикации разрешаются только подтверждённым destinations;
- индивидуальные правила групп проверяются перед каждой отправкой;
- inbound automation не смешивается с publisher queue;
- Telegram credentials не хранятся открытым текстом;
- запрещённые сценарии отсутствуют в API и UI.

**Definition of Done: выполнено.**

## 4. Этап 1 — фундамент платформы

- Python 3.12+, FastAPI, SQLAlchemy 2, Alembic;
- PostgreSQL production, SQLite development/test;
- dependency-free SPA;
- bootstrap organization/owner;
- роли owner/admin/operator/viewer;
- login/logout, refresh rotation, password change, TOTP;
- request ID, audit, TrustedHost, CORS, CSP, CSRF;
- production runtime validation;
- schema changes только через Alembic.

**Definition of Done: выполнено.**

## 5. Этап 2 — Telegram transports

### Bot API

- encrypted token;
- `getMe`, `getChat`, `getChatMember`;
- text/photo/document;
- HTML/Markdown, link preview, forum topic;
- mapping 429/403/400/network errors.

### MTProto

- собственные `api_id`/`api_hash` владельца;
- phone/code/cloud-password challenge;
- encrypted StringSession;
- identity check;
- получение уже доступных dialogs;
- text/media/topic delivery;
- revoke/pause/resume;
- одна session не запускается конкурентно несколькими независимыми workers.

### Fake gateway

- не обращается к сети;
- выдаёт deterministic `fake-*` message ID;
- используется для локального end-to-end и автоматических тестов.

**Definition of Done: выполнено; внешние credentials остаются live-gate.**

## 6. Этап 3 — destinations и контент

### Destinations

- group/supergroup/channel/forum topic;
- username, numeric chat ID и topic ID;
- permission evidence и rules URL;
- Telegram validation;
- автоматическое отключение при write forbidden;
- per-destination timezone, weekdays, start/end time и cooldown;
- корректная поддержка окна через полночь;
- расчёт ближайшего разрешённого времени.

### Bulk onboarding

- TXT, CSV и TSV до настроенного лимита;
- preview без изменения БД;
- построчные статусы `ready`, `duplicate`, `invalid`, `deferred`;
- invite-links отклоняются;
- auto-join отсутствует;
- импорт не подтверждает разрешение без evidence;
- discovery показывает только dialogs, уже доступные connection;
- выбранный dialog повторно разрешается Telegram gateway перед сохранением;
- CSV export для ревизии и резервного переноса.

### Media/templates

- size/MIME/signature/UTF-8 validation;
- защита от path traversal;
- local/S3 storage;
- SHA-256 metadata;
- optional ClamAV INSTREAM scan до storage;
- fail-closed/fail-open policy с аудитом;
- templates, parse mode, media binding и revision.

**Definition of Done: выполнено.**

## 7. Этап 4 — кампании и delivery

### Campaigns

- `once`, `daily`, `weekly`;
- IANA timezone, weekdays, start/end;
- упорядоченный список destinations;
- создание, редактирование draft/paused и копирование;
- изменение маршрута/расписания снимает прежнее утверждение;
- preview с blockers/warnings и расчётным временем;
- обязательное manual approval для MTProto;
- run now, pause, resume, cancel;
- CampaignRun и immutable DeliveryJob snapshot.

### A/B templates

- основной и дополнительный template;
- вес B от 1 до 99%;
- стабильное SHA-256 распределение по `(campaign_id, destination_id)`;
- повторяющийся запуск оставляет destination в том же варианте;
- preview показывает A/B и template ID/name;
- job фиксирует точный body/media/parse mode выбранного варианта;
- аналитика сравнивает delivery quality, но не заявляет candidate conversion без атрибуции.

### Delivery queue

- idempotency key;
- lease locking и stale recovery;
- `FOR UPDATE SKIP LOCKED` для PostgreSQL;
- bounded retry;
- worker heartbeat;
- ручной retry/cancel;
- snapshot не меняется после редактирования template.

### Safety Engine

Перед каждым сетевым запросом проверяются:

1. organization/connection/campaign state;
2. destination enabled и permission evidence;
3. Telegram validation state;
4. per-destination days/timezone/time window;
5. per-destination cooldown;
6. connection minimum interval;
7. connection daily cap;
8. organization/global hard cap;
9. FloodWait/manual review;
10. media availability.

### Ошибки

- SlowMode: defer только destination;
- FloodWait: pause connection до server wait и manual acknowledgement;
- PeerFlood/anti-spam: manual review без autoretry;
- write forbidden: destination отключается;
- auth/session revoked: connection требует переподключения;
- неопределённый сетевой результат: `waiting_review`, повтор запрещён до ручной проверки;
- явная временная ошибка до отправки: bounded retry.

**Definition of Done: выполнено.**

## 8. Этап 5 — multi-tenant

- `organization_id` во всех бизнес-сущностях;
- tenant lookup в HTTP endpoints;
- tenant scope в scheduler, safety, caps и outbox;
- tenant-scoped API keys, storage и audit;
- legacy migration в bootstrap organization;
- чужой UUID не раскрывает существование объекта.

**Definition of Done: выполнено.**

## 9. Этап 6 — Telegram Business inbox

- set/get/delete webhook;
- HTTPS URL из `PUBLIC_BASE_URL`;
- случайный path token и header secret;
- аутентификация до чтения body;
- body size limit;
- encrypted raw payload и redacted preview;
- unique `(connection, update_id)`;
- async inbound worker;
- create/edit/delete message processing;
- raw update short retention.

**Definition of Done: выполнено; live connected profile остаётся внешним gate.**

## 10. Этап 7 — диалоги, consent и кандидаты

- conversation lifecycle;
- consent requested/granted/declined/revoked;
- stop words и handoff keywords;
- active hours и allowed chat types;
- дневной лимит автоответов;
- operator assignment/manual reply;
- encrypted message body и redacted preview;
- structured candidate profile;
- encrypted phone/email;
- controlled contact reveal;
- CSV export с отдельным правом на контакты.

**Definition of Done: выполнено.**

## 11. Этап 8 — визуальные сценарии и AI

### Automation flows

- детерминированные node types: message/question/choice/handoff/end;
- graph validation: уникальные ID, достижимость, корректные переходы, отсутствие циклов;
- типизированная проверка age/phone/email/text;
- skip-if-present;
- последовательный сбор анкеты;
- revision и immutable active flow;
- привязка flow к automation policy;
- web editor, drag-and-drop, up/down fallback;
- phone/email попадают только в encrypted candidate fields.

### AI

- offline deterministic rule-based provider;
- OpenAI-compatible provider;
- encrypted API key и model allowlist;
- timeout/context/output limits;
- строгий JSON contract;
- untrusted KB separation;
- prompt injection flags;
- отсутствие auto hire/reject;
- interaction audit без секретов.

**Definition of Done: выполнено.**

## 12. Этап 9 — аналитика, интеграции и service API

### Analytics

- произвольный диапазон дат в лимитах API;
- organization timezone;
- delivery totals/status/success rate;
- daily timeseries;
- dialogs/consent/handoff;
- candidates funnel;
- AI success/latency;
- outbox delivery;
- campaign delivery;
- A/B template delivery;
- обезличенный CSV без message body и contacts.

### Integrations

- HMAC webhook;
- Google Sheets append;
- immutable CSV object;
- subscriptions по event types;
- durable outbox с target snapshot;
- per-endpoint delivery state;
- successful endpoint не вызывается повторно после ошибки соседнего;
- bounded retry/dead state;
- SSRF/DNS/private IP protection.

### Service API

- one-time API key secret;
- SHA-256 hash и prefix lookup;
- scopes;
- expiry/revoke;
- organization isolation.

**Definition of Done: выполнено.**

## 13. Этап 10 — privacy и security

### Privacy

- export/delete request;
- subject resolution по conversation/chat/user;
- encrypted export object и TTL;
- scrub identifiers, bodies и contacts;
- organization retention days;
- scheduled retention worker;
- audit без raw PII.

### Security

- AES-256-GCM с per-field AAD;
- Argon2id production profile;
- test-only настраиваемые параметры Argon2 для воспроизводимого QA;
- short JWT access и rotating hashed refresh tokens;
- refresh replay detection;
- HttpOnly/Secure/SameSite cookies;
- CSRF double-submit;
- TOTP и lockout;
- RBAC;
- список активных sessions;
- selective revoke и revoke-others;
- TrustedHost/CORS/IP allowlist;
- доверие proxy headers только от настроенного proxy;
- CSP/Frame/Referrer/Content-Type headers;
- secret redaction;
- PWA no-cache для API/auth/webhook/metrics;
- ClamAV scan до storage.

**Definition of Done: выполнено.**

## 14. Этап 11 — observability, deployment и backup

### Observability

- structured text/JSON logs;
- Prometheus build/HTTP/queue/worker/safety metrics;
- Grafana provisioning;
- alert rules;
- optional Sentry/OpenTelemetry;
- health live/ready и worker freshness.

### Deployment

- Dockerfile;
- base/prod Compose;
- one-shot migration service;
- host-level HTTPS Nginx example;
- systemd migrate/API/worker units;
- Windows/Linux dev scripts;
- `doctor` command;
- non-root/read-only container hardening.

### Backup/restore

- PostgreSQL custom dump и consistent SQLite backup;
- local storage snapshot без рекурсивного backup directory;
- Ed25519-signed receipt и signed `MANIFEST.json`;
- SHA-256 артефакта и каждого заявленного файла;
- optional/production-required `age` encryption;
- safe ZIP verification без traversal/symlink/duplicates;
- isolated SQLite restore drill;
- честный PostgreSQL metadata-only check без false full-restore claim;
- RPO/RTO policy, expiry и retained-copy checks;
- retention cleanup и signed drill evidence.

**Definition of Done: выполнено.**

## 15. Этап 12 — QA и release engineering

- 234 automated tests в 28 функциональных модулях;
- 18 независимых coverage-процессов;
- statement coverage 79,70%, gate 65%;
- compileall;
- JavaScript syntax check для SPA и service worker;
- Alembic fresh upgrade/check, downgrade 2.2→2.1 и повторный upgrade к head 2.2;
- HTTP login/CSRF/dashboard/logout smoke;
- isolated worker cycle;
- audit-chain verify и master-key rotation dry-run;
- backup→verify→isolated restore recovery smoke;
- deterministic timeout для каждого functional module и coverage process;
- manifest/forbidden-file scan на release archive;
- PWA resource/no-sensitive-cache tests;
- release asset validator для version markers, Compose, JSON, локальных ссылок, SLO assets и forbidden paths.

**Definition of Done: выполнено.**

## 16. Этап 13 — Governance & Resilience

### Explicit approval

- `run-now` не создаёт разрешение сам;
- запрос утверждения является отдельной сущностью;
- normal route требует минимум одно решение owner/admin;
- high-risk route выше порога требует настраиваемое число решений;
- four-eyes запрещает автору запроса утверждать собственный запуск;
- запрос имеет TTL и проактивно закрывается worker;
- недостаток независимых approvers обнаруживается до создания запроса;
- mutation endpoints используют row locking и uniqueness constraint.

### Approval fingerprint

- SHA-256 фиксирует connection policy, templates/revisions/body hashes, media, schedule, destinations, permission evidence, windows и cooldown;
- edit/copy-route replacement/permission change аннулируют разрешение;
- Safety Engine пересчитывает fingerprint непосредственно перед delivery;
- stale approval переводит кампанию обратно в draft и создаёт уведомление.

### Emergency stop

- owner/admin останавливает всю организацию с обязательной причиной;
- `pending/retry` jobs переходят в `waiting_review`;
- scheduler пропускает paused organization;
- Safety Engine блокирует сеть даже для уже арендованного job;
- resume повторно ставит удержанные jobs в очередь без обхода остальных проверок;
- UI показывает глобальную красную полосу и действие возобновления.

### Notifications

- tenant-scoped unread/read/acknowledged;
- info/warning/critical severity;
- deduplication window;
- dashboard/topbar counters;
- notifications для Telegram restrictions, ambiguous delivery, auth loss, approval lifecycle и emergency stop;
- `notification.created` отправляется через существующий durable outbox.

### Audit integrity

- per-organization и system SHA-256 hash-chain;
- monotonic sequence, `prev_hash`, `entry_hash`, `chain_version`;
- verification из API/UI/CLI;
- обнаружение модификации записи и удаления chain state;
- offline backfill legacy audit rows;
- внешний immutable export остаётся рекомендуемым production control.

### Master-key rotation

- preflight decrypt всех поддерживаемых encrypted fields;
- dry-run без изменения данных;
- валидация нового ключа;
- DB transaction и best-effort rollback внешних encrypted export objects;
- critical system audit без plaintext secrets;
- документированный stop/backup/rotate/update-env/start процесс.

**Definition of Done: выполнено.**

## 17. Этап 14 — Controlled Operations

### Permission lifecycle

- destination хранит `permission_reviewed_at` и необязательный `permission_expires_at`;
- подтверждение разрешения фиксирует reviewer/time в audit;
- `unverified`/`denied` очищают срок и запрещают публикацию;
- preflight предупреждает о скором окончании и блокирует истёкшее разрешение;
- Safety Engine повторяет проверку непосредственно перед Telegram network call.

### Persisted preflight

- отдельный отчёт с TTL, campaign fingerprint, blockers/warnings и item-level результатами;
- content fingerprint, batch, due time, permission expiry и duplicate job сохраняются для каждой группы;
- approval submission и manual run выполняют preflight;
- scheduler **всегда** создаёт новый отчёт перед run, потому что expiry/duplicates/cooldowns зависят от времени;
- blocked report сохраняется вместе с audit, а кампания ставится на паузу.

### Duplicate guard

- SHA-256 по body, parse mode, media hash и link preview;
- поиск только в рамках organization/destination и настроенного lookback;
- проверка в preflight и Safety Engine;
- duplicate job получает `skipped / DUPLICATE_CONTENT` без обращения к Telegram;
- механизм не изменяет текст и не используется для обхода модерации.

### Staged rollout

- `standard` и `staged` режимы кампаний;
- configurable batch size, pause, manual checkpoint и failure threshold;
- первый пакет `pending`, остальные `held`;
- worker не арендует `held` jobs;
- следующий пакет раскрывается только checkpoint endpoint или безопасным automatic release;
- превышение порога ошибок и любой `waiting_review` останавливают переход;
- controlled abort отменяет ожидающие пакеты, ставит кампанию на паузу и снимает approval.

### Ambiguous delivery reconciliation

- `DELIVERY_RESULT_UNCERTAIN` блокирует connection/job без automatic retry;
- owner/admin выбирает `confirmed_sent`, `confirmed_not_sent` или `skipped`;
- `confirmed_sent` требует Telegram message ID;
- `confirmed_not_sent` повторно проверяет connection, permission и approval перед явным retry;
- reviewer/time/note сохраняются в job и audit.

### UI и эксплуатация

- срок разрешения в destination form/table;
- кнопки preflight и история runs;
- batch/checkpoint/abort controls;
- `held` и content fingerprint в очереди;
- отдельная форма ручной сверки неоднозначной доставки;
- документ [CONTROLLED_OPERATIONS.md](CONTROLLED_OPERATIONS.md).

**Definition of Done: выполнено.**

## 18. Этап 15 — Production Pilot 1.4

### Destination validation lifecycle

- `validated_at` и `validation_expires_at` сохраняются отдельно от permission evidence;
- immutable history фиксирует capabilities, результат, источник, оператора и ошибку;
- single/batch validation выполняется через фактический Telegram gateway;
- batch ограничен 50 назначениями и прекращает новые запросы после `retry_after`;
- `write_forbidden` классифицируется отдельно, отключает destination и требует ручной проверки;
- legacy `validated=true` получает исходную дату из `updated_at`, но требует live revalidation.

### Publishing blackout calendar

- scope: organization, connection, destination;
- kind: one-time UTC или weekly IANA-timezone;
- overnight weekly windows поддерживаются без разбиения на два правила;
- несколько совпавших окон объединяются, а job переносится на самое позднее окончание;
- Safety Engine принимает решение до построения Telegram gateway и не увеличивает attempt counter;
- CRUD/evaluation защищены RBAC, tenant isolation, CSRF и audit.

### Pilot readiness

- сохраняемый report с campaign fingerprint и TTL;
- проверки organization switch, worker heartbeat, connection status/health/credentials;
- актуальность approval и свежий persisted preflight;
- approver capacity, staged threshold, destination validation freshness, blackout state, caps и admin TOTP;
- `passed`, `warning`, `blocked` с машиночитаемыми checks и человекочитаемыми blockers/warnings;
- `run-now`/`resume` блокируются без актуального report при включённой policy;
- scheduler ставит due-кампанию на паузу и создаёт critical audit/notification;
- Web UI объединяет readiness, validation attention и operational calendar.

### Исправления надёжности

- attempt count увеличивается один раз на фактический сетевой запрос;
- custom destination title не теряется при create/bulk onboarding;
- PATCH blackout очищает несовместимые поля предыдущего scope/kind;
- `CHAT_WRITE_FORBIDDEN` не теряется как общий validation failure.

Документ: [PRODUCTION_PILOT.md](PRODUCTION_PILOT.md).

**Definition of Done: выполнено; реальные Telegram checks остаются live-gate.**

## 19. Этап 16 — Pilot Certification & Diagnostics 1.5

### Последовательный допуск масштаба

- stages: `LOCAL`, `SERVICE`, `FIVE`, `TWENTY`, `FIFTY`, `HUNDRED`;
- максимальный маршрут: 0/1/5/20/50/100 назначений;
- local допускает широкий маршрут только в fake mode без Telegram network call;
- gate применяется в preview, approval, run-now, resume, scheduler и Safety Engine;
- production runtime требует `PILOT_STAGE_ENFORCEMENT_REQUIRED=true`;
- снижение этапа приостанавливает несовместимые кампании и переводит ожидающие jobs в review.

### Canary и доказательство этапа

- фиксированный служебный текст с уникальным marker;
- пользователь не вводит рекламный body;
- один Telegram request и отсутствие automatic retry;
- destination/connection row locks защищают от параллельного двойного запуска;
- fake canary и `fake-*` message IDs не считаются live evidence;
- stage assessment требует свежий worker, здоровье собственного connection каждого назначения, permissions, validation, canary, отсутствие uncertain jobs/critical notifications и реальный предыдущий run;
- assessment имеет TTL и SHA-256 state fingerprint, который инвалидируется при потере worker heartbeat или новом critical event;
- повышение/понижение требуют точную confirmation phrase и audit.

### Safe support bundle

- временный ZIP со статусами runtime/organization/connections/destinations/campaigns/queue/pilot/notifications/audit integrity;
- внутренние UUID заменены псевдонимами;
- исключены credentials, tokens, session strings, PII, names, chat IDs, usernames, message bodies и raw audit details;
- внутренний `MANIFEST.sha256`, внешний SHA-256 и `Cache-Control: no-store`;
- TTL cleanup и ручное удаление;
- доступ только Owner/Admin.

Документ: [PILOT_CERTIFICATION.md](PILOT_CERTIFICATION.md).

**Definition of Done: кодовый контур выполнен; реальная canary и stage evidence остаются live-gate.**

## 20. Этап 17 — Commissioning & Portability 1.6

### Инфраструктурная приёмка

- persisted report с TTL, checks, blockers, warnings и SHA-256 fingerprint;
- DB connectivity и соответствие Alembic head;
- storage write/read/delete round-trip;
- Redis/local lock backend и worker heartbeat;
- audit-chain integrity, admin TOTP, HTTPS, backup freshness, pg_dump и ClamAV policy;
- development warnings не выдаются за production readiness.

### Формальная программа пилота

- local fake stage добавляется автоматически;
- live-этапы выбираются только из `1/5/20/50/100` и идут последовательно;
- старт требует точного route size, актуальных commissioning/approval/readiness и неизменившегося campaign fingerprint;
- конкретный completed campaign run становится immutable evidence snapshot;
- evidence получает SHA-256 и повторно проверяется перед sign-off;
- live stage может требовать решения другого Owner/Admin;
- JSON acceptance report содержит собственный payload checksum.

### Безопасный перенос

- export/import reference configuration без Telegram sessions/tokens/API keys;
- strict ZIP path/duplicate/symlink/encryption/manifest/schema validation;
- optional media переносится только после повторной MIME/magic/SHA-256/ClamAV-проверки;
- imported connections не имеют credentials; destinations выключены/unverified; campaigns draft; automations disabled;
- старые approvals, permissions, jobs, conversations и PII не переносятся;
- download tenant-scoped/no-store, deletion очищает storage и фиксируется в audit.

Документ: [COMMISSIONING_AND_PORTABILITY.md](COMMISSIONING_AND_PORTABILITY.md).

**Definition of Done: кодовый контур выполнен; live sign-off требует реальных Telegram credentials и разрешённых групп.**

## 21. Этап 18 — Artifact Trust & Signed Evidence 1.7

### Ключи и доверие

- tenant-scoped Ed25519 signing keys;
- локальная приватная часть зашифрована AES-256-GCM с field-specific AAD;
- public PEM/base64 export без выдачи private material;
- явный `trusted_for_import`, основной signing key и локальный trust store;
- необратимый revoke уничтожает приватную часть, но сохраняет public fingerprint для истории;
- переключение default key сериализуется на уровне organization row.

### Подписанные артефакты

- configuration bundle подписывает canonical `manifest.json`;
- support bundle подписывает точные байты `MANIFEST.sha256`;
- pilot acceptance report подписывает canonical payload;
- разные artifact classes имеют разные domain-separated `purpose`;
- signature envelope защищает purpose, key ID, fingerprint, public key, digest и timestamp;
- API, UI и автономный CLI показывают integrity, validity, trust и revoke status.

### Политика и эксплуатация

- `optional` сохраняет контролируемую legacy-совместимость;
- `require_valid` требует криптографически корректную подпись;
- `require_trusted` требует активный explicitly trusted key;
- production startup принудительно требует `require_trusted`;
- commissioning проверяет default private key и trust status;
- master-key rotation повторно шифрует private signing key без вывода plaintext;
- release-QA проверяет payload/manifest/signature tampering, cross-tenant trust и offline CLI.

Документ: [ARTIFACT_TRUST.md](ARTIFACT_TRUST.md).

**Definition of Done: кодовый контур выполнен; независимая передача public fingerprint и внешняя юридическая подпись остаются deployment-specific.**

## 22. Этап 19 — Recovery Assurance & Continuity 1.8

### Политика и evidence

- tenant-scoped RPO/RTO, drill age, retained count и production requirements;
- отдельные состояния `registered` и `verified`;
- только физически проверенный archive участвует в RPO;
- backup/drill evidence сохраняется с tenant binding и audit.

### Backup trust

- consistent SQLite copy или PostgreSQL custom dump;
- local storage snapshot без рекурсивного backup directory;
- signed manifest, signed receipt и SHA-256 каждого файла;
- optional/required age encryption;
- safe ZIP extraction и strict manifest composition.

### Restore assurance

- isolated SQLite drill;
- PostgreSQL metadata-only mode без ложного full-restore claim;
- signed drill receipt, duration и RTO result;
- failed drill сохраняется, но не выполняет compliance;
- non-destructive CLI wrappers;
- raw backup/age identity не проходят через web.

### Production integration

- recovery assurance входит в commissioning;
- production startup требует encryption, trusted signature и age recipient;
- web-панель показывает policy/status/history;
- release-QA выполняет backup→verify→drill smoke.

Документы: [RECOVERY_ASSURANCE.md](RECOVERY_ASSURANCE.md), [BACKUP_RESTORE.md](BACKUP_RESTORE.md).

**Definition of Done: кодовый контур выполнен; полный PostgreSQL disposable restore, off-site immutable storage и key escrow остаются deployment-specific live-gates.**

## 23. Этап 20 — Change Management & Upgrade Assurance 1.9

### Управляемые изменения

- tenant-scoped change request для upgrade/configuration/database migration/infrastructure;
- reason, risk summary, rollback plan и плановое окно являются частью fingerprint;
- автор не может сам утвердить собственное изменение;
- статусы `draft → approved → in_progress → completed/failed`, отдельный `cancelled`;
- завершение/ошибка/отмена фиксируются в audit-chain.

### Maintenance mode

- отдельный от emergency stop режим обслуживания;
- scheduler исключает организации в maintenance;
- run-now/resume блокируются;
- Safety Engine повторяет gate непосредственно перед Telegram network call;
- active change запрещает выключить maintenance преждевременно.

### Deployment verification

- persisted pre/post reports с TTL и SHA-256 fingerprint;
- Alembic current/head drift;
- current/target application version;
- отсутствие processing delivery перед upgrade;
- tenant audit-chain integrity;
- post-change worker heartbeat;
- production drift становится blocker, dev auto-schema честно маркируется warning.

Документ: [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md).

**Definition of Done: кодовый контур выполнен; фактический deploy/rollback остаётся операцией владельца инфраструктуры по утверждённому runbook.**

## 24. Этап 21 — Release Trust & Provenance 2.0

### Доверие к сборке

- tenant-scoped `ReleaseAttestation`;
- canonical JSON payload и SHA-256;
- BUILD_INFO, MANIFEST и requirements hashes;
- нормализованный inventory runtime/dev dependencies;
- Ed25519 signature с отдельным purpose `teleflow-release-attestation-v1`;
- повторная проверка через trust store организации;
- tamper detection и revoked-key rejection.

### Change-management binding

- `ChangeRequest.release_attestation_id`;
- fingerprint change request включает выбранный attestation;
- pre-change verification проверяет совпадение target version;
- production требует `TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION=true`;
- unknown/untrusted/revoked/tampered release блокирует upgrade до начала deployment.

### Web/API

- отдельный раздел «Доверие к релизам»;
- создание подписи текущей сборки;
- импорт signed attestation;
- повторная trust verification;
- выбор attestation в форме upgrade change request.

Документ: [RELEASE_TRUST.md](RELEASE_TRUST.md).

**Definition of Done: кодовый контур выполнен; внешний transparency log, reproducible-build verification и онлайн CVE scanning остаются CI/infrastructure-specific.**


## 25. Этап 22 — Supply Chain Transparency & Dependency Assurance 2.1

### Transparency

- signed append-only publish/withdraw log для release attestations;
- sequential SHA-256 hash-chain;
- Ed25519 signature каждой записи;
- проверка sequence, previous hash, payload, attestation digest, version и source commit;
- запрет двух одновременно опубликованных attestations одной версии.

### Dependency evidence

- детерминированный CycloneDX 1.6 SBOM;
- строгий нормализованный inventory/vulnerability report;
- подпись отчёта с отдельным cryptographic purpose;
- tenant policy: exact pins, prerelease, severity limits, TTL и denied packages;
- immutable assessment с report/SBOM/policy/attestation hashes;
- tamper detection и expiration;
- inventory-only не считается vulnerability scan.

### Change-management binding

- `ChangeRequest.release_dependency_assessment_id`;
- fingerprint включает attestation и assessment;
- pre/post deployment checks `release_transparency` и `dependency_assurance`;
- production требует trusted attestation, published transparency entry, trusted current assessment и настоящий scan.

Документ: [SUPPLY_CHAIN_ASSURANCE.md](SUPPLY_CHAIN_ASSURANCE.md).

**Definition of Done: кодовый контур выполнен; advisory feed, scanner execution и внешний WORM transparency остаются CI/infrastructure-specific.**


## 26. Этап 23 — Operational SLO & Incident Assurance 2.2

### SLO и error budget

- tenant-scoped policy для delivery target, evaluation window, sample size и TTL;
- расчёт sent/failed/waiting-review без ложного подтверждения неопределённой доставки;
- error budget в basis points и отдельные warning/critical thresholds;
- queue age, worker heartbeat, unresolved reviews и critical incidents;
- immutable assessment с policy snapshot/hash, checks, metrics и fingerprint;
- ручная и периодическая worker evaluation под distributed lock.

### Действующие gates

- ручной run-now/resume;
- scheduler перед созданием campaign run/jobs;
- Safety Engine непосредственно до Telegram network call;
- pre/post deployment verification;
- commissioning;
- production startup требует `TELEFLOW_SLO_GATE_REQUIRED=true`.

### Incident management

- статусы open/acknowledged/mitigating/resolved/closed;
- ручные и автоматические источники;
- отдельная append-only event history;
- owner, impact, root cause, resolution и postmortem URL;
- stable dedup key для каждого blocked SLO check;
- автоматическое разрешение только после новой подтверждающей оценки;
- RBAC, tenant isolation, notifications и audit-chain.

### Observability

- Prometheus SLO/error-budget/incident metrics;
- critical alerts;
- Grafana panels;
- веб-раздел «Надёжность и инциденты».

Документ: [OPERATIONAL_SLO.md](OPERATIONAL_SLO.md).

**Definition of Done: кодовый контур выполнен; production thresholds и фактические error-budget данные принимаются на live-инфраструктуре владельца.**

## 27. Этап 24 — Execution Fencing & Failover Assurance 2.3

### Split-brain prevention

- один execution lease на организацию;
- active/standby registry с heartbeat и версией;
- монотонный fencing epoch;
- takeover только после expiry или явного controlled failover;
- PostgreSQL row lock удерживается до завершения Telegram-вызова;
- standby scheduler не создаёт delivery jobs.

### Durable network evidence

- отдельный `delivery_attempts` ledger;
- `prepared` коммитится до сети;
- `network_started` коммитится непосредственно перед Telegram gateway;
- падение до сети допускает безопасный retry;
- падение после начала сети требует ручной сверки;
- attempt numbers не переиспользуются после Slow Mode или временной ошибки.

### Controlled failover

- source lease переводится в `draining`;
- target обязан иметь свежий heartbeat;
- processing и uncertain attempts блокируют переключение;
- независимый Owner/Admin вводит точную фразу;
- переключение увеличивает epoch и лишает старый worker права на сеть;
- отмена возвращает source в active без скрытого продолжения jobs.

### Operations

- отдельный веб-раздел «Active / Standby»;
- immutable-like attempt history;
- Prometheus metrics и alerts;
- commissioning execution check;
- production требует включённый fencing и distinct failover approver.

Документ: [EXECUTION_FENCING.md](EXECUTION_FENCING.md).

**Definition of Done: кодовый контур выполнен; реальное переключение между двумя хостами с общей PostgreSQL и Telegram test chat остаётся live-gate владельца.**

## 28. Внешние live-gates

Эти пункты невозможно честно закрыть внутри исходного архива без секретов и инфраструктуры владельца:

1. Реальный Bot API token и служебная группа.
2. Реальные `api_id`, `api_hash`, номер и MTProto challenge.
3. Telegram Business profile и connected bot.
4. Пять destinations с подтверждённым разрешением.
5. Реальный OpenAI-compatible endpoint при его использовании.
6. Google service account/spreadsheet при использовании Sheets.
7. S3 credentials при использовании object storage.
8. Доступный `clamd` при включённом antivirus mode.
9. Production domain, TLS, PostgreSQL, Redis и backup target.
10. Фиксация реальных message IDs, update IDs и restore drill.

Процедура: [PILOT_CHECKLIST.md](PILOT_CHECKLIST.md).

## 29. Порядок ввода в эксплуатацию

```text
fake environment
  → 1 служебная группа
  → 5 разрешённых групп
  → 20 групп
  → 50 групп
  → 100 групп
```

Каждый пакет принимается отдельно. У каждой группы сохраняются собственные evidence, expiry, topic, timezone, schedule и cooldown. Переход к следующему масштабу выполняется staged-пакетами через checkpoint. Масштабирование не отменяет Telegram limits и правила конкретных сообществ.

## 29. Что сознательно не входит в финальный core roadmap

Следующие направления являются отдельными продуктами или инфраструктурными проектами и не нужны для исходного запроса:

- SaaS billing, публичная регистрация и маркетплейс тарифов;
- нативные iOS/Android-приложения — PWA уже покрывает установку панели;
- Kubernetes/Helm/autoscaling;
- локализация на несколько языков;
- неограниченное горизонтальное partitioning одной MTProto-сессии;
- автоматический сбор групп/участников;
- массовые личные сообщения;
- ротация аккаунтов, устройств или прокси для обхода ограничений;
- скрытая модификация текста для обхода Telegram filters.

Эти пункты не являются незавершёнными функциями релиза 2.2.


## 30. Этап 26 — Capacity & Backpressure Assurance

- tenant-scoped ordered limits для active/ready/processing jobs и active runs;
- immutable assessment evidence с policy fingerprint и TTL;
- admission check в API и scheduler до queue mutation;
- transactional recheck перед созданием CampaignRun/DeliveryJob;
- staged ready-budget и атомарный manual retry;
- exact minute/hour dispatch budgets;
- durable PREPARED reservation до Telegram gateway;
- concurrency race rejection без сетевого вызова;
- estimated drain time с rate-window delay;
- periodic worker assessments;
- Prometheus, alerts, Grafana и 32-й раздел SPA;
- production gate `TELEFLOW_CAPACITY_ASSURANCE_REQUIRED=true`.

**Definition of Done:** автоматизированный контур реализован; реальная пропускная способность подтверждается нагрузочным пилотом на целевой PostgreSQL/Redis/worker-инфраструктуре.
