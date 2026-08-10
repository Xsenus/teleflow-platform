# Security design TeleFlow Platform 2.1

## 1. Принципы

- deny by default;
- минимальные права Telegram, API keys и операторов;
- tenant isolation на backend и worker;
- секреты никогда не возвращаются read API;
- fail-safe stop вместо обхода Telegram limits;
- публикация только в destinations с подтверждённым разрешением;
- PII хранится только в необходимом объёме и ограниченный срок;
- production не стартует с небезопасной конфигурацией.

## 2. Секреты и шифрование

AES-256-GCM защищает:

- Bot API token;
- MTProto `api_hash` и StringSession;
- временные auth challenge data;
- Business webhook path/header secrets;
- candidate phone/email;
- full conversation bodies;
- AI API keys;
- Google service-account config;
- integration secrets;
- privacy export payloads.

Каждое поле использует отдельный AAD context. Ciphertext нельзя без ошибки перенести в другое поле или объект. `TELEFLOW_MASTER_KEY` не хранится в БД и должен находиться в secret manager или закрытом environment.

При компрометации master key следует считать раскрытыми все зашифрованные данные из доступной копии БД/storage. Версия 1.2 предоставляет `scripts/rotate_master_key.py`: сначала выполняется полный preflight decrypt, затем dry-run, остановка API/worker, проверенный backup и только после этого реальное re-encryption. Новый ключ не выводится в audit/stdout и должен быть установлен как `TELEFLOW_MASTER_KEY` до повторного запуска. DB transaction не делает внешнее S3-хранилище crash-atomic, поэтому storage changes имеют rollback journal и требуют restore drill.

## 3. Пароли, browser sessions и TOTP

- Argon2id password hashing;
- production использует сильный профиль Argon2;
- уменьшенные Argon2 параметры разрешены только изолированным test fixtures;
- access JWT с коротким TTL;
- refresh token хранится только как SHA-256 hash;
- rotation на каждом refresh;
- replay detection отзывает token family;
- HttpOnly cookies;
- Secure в production;
- SameSite Strict по умолчанию;
- double-submit CSRF для изменяющих cookie requests;
- login/TOTP lockout;
- обязательный TOTP для owner/admin в production.

Пользователь видит собственные активные sessions с датой создания, последней активностью, IP и user-agent preview. Доступны точечный revoke и `revoke-others`. Raw refresh token, полный JWT и TOTP secret не возвращаются после предусмотренного setup step.

Bootstrap password используется только для первого входа и должен быть немедленно заменён.

## 4. RBAC

| Действие | Owner | Admin | Operator | Viewer |
|---|---:|---:|---:|---:|
| Просмотр рабочих разделов | ✓ | ✓ | ✓ | ✓ |
| Connections и credentials | ✓ | ✓ | — | — |
| Destinations/templates/drafts | ✓ | ✓ | ✓ | — |
| Submit campaign approval request | ✓ | ✓ | ✓ | — |
| Decide/run campaign | ✓ | ✓ | — | — |
| Organization emergency stop | ✓ | ✓ | — | — |
| Business/AI policies/flows | ✓ | ✓ | ограниченно | — |
| Candidates/operator work | ✓ | ✓ | ✓ | согласно endpoint policy |
| Audit/privacy/integrations | ✓ | ✓ | — | — |
| API keys/users | ✓ | частично | — | — |

Права проверяются FastAPI dependencies и tenant-filtered queries. Скрытие кнопки в SPA не является security control.

## 5. Tenant isolation

Любой запрос к бизнес-объекту содержит organization filter. API key привязан к одной организации. Worker не доверяет `organization_id` из webhook payload, CSV import или external event. Analytics, hard caps, storage paths, outbox и privacy работают в tenant scope.

Тесты проверяют доступ по чужому UUID и отсутствие межорганизационного расходования лимитов.

## 6. Telegram credentials и user account

- владелец вводит credentials только через HTTPS-панель;
- code/cloud password существуют внутри TTL challenge;
- StringSession зашифрована;
- token/session/phone не входят в DTO, audit и logs;
- revoke прекращает возможность использования connection;
- при утечке нужно отозвать Telegram sessions/token и заменить platform secrets.

MTProto не используется для auto-join, массовых DMs, account rotation, proxy rotation и обхода FloodWait. Discovery возвращает только dialogs, к которым connection уже имеет доступ, и не подтверждает право публикации автоматически.

## 7. Destination onboarding и разрешения

Bulk import рассматривается как недоверенный ввод:

- ограничение размера и количества строк;
- UTF-8/CSV parsing;
- invite-links отклоняются;
- public username/link/chat ID нормализуются;
- preview не меняет БД;
- duplicate обрабатывается безопасно;
- `permission_confirmed=true` требует note или rules URL;
- destination всё равно проходит Telegram validation.

Ни импорт, ни discovery не вступают в группу и не обходят правила сообщества.

## 8. Delivery safety

Перед каждым send повторно проверяются organization emergency-stop, актуальность campaign approval fingerprint, connection, campaign, permission, Telegram validation, individual window, cooldown, minimum interval и caps. Error policies разделены:

- SlowMode откладывает только destination;
- FloodWait приостанавливает connection;
- anti-spam/PeerFlood требует ручной проверки и не имеет autoretry;
- write forbidden отключает destination;
- uncertain network result переводит job в `waiting_review` без автоматического повтора.

A/B templates применяются только как стабильный content experiment. Они не используются для случайной мутации текста или обхода filters.

## 9. Governance и контролируемый допуск

### Approval policy

- `run-now` и `resume` не создают разрешение автоматически;
- operator может подготовить draft и запросить утверждение, но решение принимает только owner/admin;
- four-eyes запрещает requester самостоятельно одобрить собственную кампанию;
- high-risk threshold включает multi-approval;
- запрос имеет TTL и закрывается worker с audit/notification;
- решения сериализуются row locks и защищены уникальностью пользователя;
- backend отказывает в policy, которую невозможно выполнить текущим числом approvers.

### Approval fingerprint

Разрешение связано не только со строкой `approved=true`, а с SHA-256 fingerprint защищённой конфигурации. Изменение body/template revision/media, маршрута, порядка, schedule, connection safety settings или destination permission/window/cooldown снимает разрешение. Safety Engine пересчитывает fingerprint перед gateway call, поэтому stale browser или уже запущенный scheduler не может отправить изменённый payload по старому решению.

### Emergency stop

Tenant-level stop проверяется API, scheduler и delivery worker. Pause переводит ещё не отправленные jobs в `waiting_review`, создаёт critical notification/audit/outbox и сохраняет reason/actor/time. Resume не обходит approval, destination и Telegram safety checks.

## 10. Telegram Business webhook

Endpoint защищён двумя случайными значениями:

1. path token;
2. `X-Telegram-Bot-Api-Secret-Token`.

Сначала проверяются connection status, path и constant-time header; затем читается body. Есть body-size limit, JSON validation, deduplication и Nginx rate limit. Raw update зашифрован.

Webhook URL должен быть HTTPS и не публиковаться в logs/tickets.

## 11. Visual flows и AI security

### Flows

- definition проходит schema и graph validation;
- циклы, недостижимые узлы и неизвестные transitions отклоняются;
- active revision неизменяема;
- field allowlist не позволяет произвольную запись в модель;
- phone/email передаются только в encrypted candidate fields;
- пользовательский текст не исполняется как код.

### AI

- candidate text и knowledge base считаются untrusted data;
- system prompt запрещает следовать инструкциям из untrusted content;
- prompt injection patterns фиксируются в safety flags;
- context ограничен текущими tenant/conversation;
- provider key зашифрован;
- model allowlist;
- strict JSON contract;
- AI не принимает окончательное hire/reject решение;
- handoff доступен пользователю и оператору;
- внешний AI включается после оценки DPA, региона и retention провайдера.

## 12. SSRF и внешние endpoints

Integration/AI URL проходит:

- scheme check;
- запрет embedded credentials;
- DNS resolution;
- запрет localhost/private/link-local/multicast/reserved IP;
- HTTPS requirement в production.

`TELEFLOW_ALLOW_PRIVATE_INTEGRATION_URLS=true` допустим только в контролируемой сети. DNS rebinding остаётся residual risk; egress firewall рекомендуется ограничить нужными domains/IP ranges.

## 13. PII и аналитика

- списки содержат redacted preview;
- full message body и contacts раскрываются отдельными audited endpoints;
- contact access требует роль или scope;
- consent state хранится явно;
- privacy export/delete;
- retention ограничивает срок хранения;
- audit не содержит raw body/token/contact;
- analytics использует агрегаты и не расшифровывает тексты/контакты;
- analytics CSV не содержит message body и PII.

## 14. Upload, ClamAV и storage

Upload проверяет:

1. размер;
2. allowlisted MIME/extension;
3. file signature;
4. UTF-8 для text formats;
5. optional ClamAV scan;
6. generated storage key;
7. SHA-256.

ClamAV adapter использует TCP `INSTREAM`, не shell-команды. Malware block фиксируется critical audit event. При scanner outage:

- `ANTIVIRUS_FAIL_CLOSED=true` — upload отклоняется;
- `false` — upload допускается только как явно принятый риск, событие фиксируется в audit.

Local keys проходят traversal protection. Production storage/backup directory имеет закрытые permissions. Для S3 рекомендуются private bucket, TLS, versioning, server-side encryption и минимальный IAM policy.

## 15. PWA и browser cache

Service worker кэширует только статические файлы приложения и offline page. Он никогда не кэширует:

- `/api/*`;
- `/hooks/*`;
- `/metrics`;
- non-GET requests;
- auth responses;
- candidate/conversation content;
- dynamic HTML responses с пользовательскими данными.

Offline page статична и не показывает данные последней session. Очистка browser profile остаётся обязанностью владельца устройства при его передаче другому человеку.

## 16. HTTP hardening

- TrustedHost;
- exact CORS allowlist;
- optional IP/CIDR allowlist;
- proxy headers доверяются только при явной настройке trusted proxy;
- CSP без внешних CDN;
- `X-Frame-Options: DENY`;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: no-referrer`;
- HSTS на host-level TLS proxy;
- login/API/webhook rate limits;
- внешний `/metrics` закрыт.

## 17. Container/systemd

Docker:

- non-root user;
- read-only filesystem;
- dropped capabilities;
- `no-new-privileges`;
- tmpfs `/tmp`;
- internal DB/Redis networks;
- one-shot migration service.

Systemd:

- separate user;
- `ProtectSystem=strict`;
- `ProtectHome`, `PrivateTmp`, `PrivateDevices`;
- restricted address families;
- explicit writable storage/data paths.

## 18. Logging и audit

Application logs не содержат request/response bodies с credentials. Audit фиксирует actor, action, entity, request ID, IP и redacted details. Каждая новая запись входит в tenant/system SHA-256 hash-chain с sequence/prev_hash/entry_hash. `GET /audit/verify` и `scripts/audit_chain.py` обнаруживают изменение защищённых полей, нарушение последовательности и отсутствие chain state. Для legacy rows backfill выполняется offline при остановленных writers. Hash-chain не заменяет внешний immutable/WORM sink.

Security-significant events включают:

- login/TOTP failures;
- refresh replay;
- session revoke;
- connection pause/manual review;
- campaign approval request/decision/expiry/invalidation;
- organization emergency stop/resume;
- critical notification acknowledgement;
- audit chain backfill/verification failure;
- master-key rotation;
- permission changes;
- PII/contact reveal;
- malware detection/scanner bypass;
- privacy export/delete;
- integration/API-key changes.

Sentry/OTel перед включением должны получить scrubbing/denylist.

## 19. Production checklist

- [ ] HTTPS и HSTS;
- [ ] PostgreSQL, не SQLite;
- [ ] `AUTO_CREATE_SCHEMA=false`;
- [ ] random master/JWT/bootstrap/DB/Redis secrets;
- [ ] fake mode выключен;
- [ ] owner/admin TOTP включён;
- [ ] four-eyes и high-risk approval policy соответствуют числу администраторов;
- [ ] emergency stop проверен до live Telegram;
- [ ] historical audit backfill и verify выполнены;
- [ ] master-key rotation dry-run проверен на копии backup;
- [ ] active sessions проверены, лишние отозваны;
- [ ] exact CORS/hosts;
- [ ] trusted proxy ограничен;
- [ ] backup зашифрован и вынесен с сервера;
- [ ] restore drill выполнен;
- [ ] `/metrics` доступен только внутри;
- [ ] S3 private/egress rules проверены;
- [ ] ClamAV fail-closed включён, если upload публичный;
- [ ] PWA cache policy проверена;
- [ ] Telegram permissions минимальны;
- [ ] пять разрешённых groups прошли pilot;
- [ ] incident contacts и revoke procedure известны.

## 20. Controlled Operations security 1.3

- Истёкшее разрешение блокируется в preflight, scheduler и worker до Telegram network call.
- Срок/ревизия разрешения включены в approval fingerprint, поэтому их изменение снимает утверждение.
- Preflight имеет TTL и не является пропуском для worker; фактический запуск всегда получает новый отчёт.
- `held` не входит в selectable job statuses и не раскрывается обычным resume/retry.
- Checkpoint и abort доступны только Owner/Admin, выполняются под блокировкой run и попадают в tamper-evident audit.
- Неоднозначный delivery result не повторяется автоматически и не закрывается job/campaign cancel, staged abort или connection revoke.
- `confirmed_sent` требует Telegram message ID; `confirmed_not_sent` требует активное подключение, действующее разрешение и актуальный approval.
- Duplicate guard сравнивает SHA-256 snapshot, а не хранит отдельную копию персональных данных.
- Content fingerprint не используется как секрет или доказательство доставки; это локальный ключ равенства утверждённого содержимого.

Production policy рекомендует staged mode, ручной checkpoint и ненулевой duplicate guard для любого первого live-маршрута. Отключение этих механизмов должно быть отдельным журналируемым risk acceptance.

## 21. Production Pilot security 1.4

### Validation freshness is separate from permission evidence

Разрешение администратора группы и техническая доступность Telegram — разные факты. `permission_status` не становится подтверждённым после API-проверки, а успешный `resolve_destination` не создаёт юридическое или организационное разрешение на публикацию.

Техническая проверка имеет TTL. После его истечения preflight, readiness и Safety Engine блокируют отправку. `write_forbidden` отключает назначение, сохраняет отдельный history event и требует ручного анализа.

### Blackout before network boundary

Publishing blackout проверяется до создания gateway и до изменения attempt counter. Это позволяет использовать календарь как аварийный и регламентный control, а не как post-factum журнал.

Blackout CRUD разрешён Owner/Admin. Все ссылки connection/destination проверяются в текущей организации. При изменении scope/kind backend очищает несовместимые поля и валидирует полную объединённую модель.

### Short-lived readiness authorization

Readiness report не является долгоживущим токеном. Он действителен только при совпадении fingerprint и до `expires_at`. Blocked report никогда не разрешает запуск. Warning допускается только как явно наблюдаемое состояние; production policy может дополнительно ужесточить этот режим в отдельной конфигурации.

Production startup validation требует `TELEFLOW_PILOT_READINESS_REQUIRED=true`. Scheduler не пытается автоматически создать readiness от имени пользователя: при отсутствии актуального отчёта он ставит кампанию на паузу и сообщает оператору.

### Audit and data minimization

History хранит capabilities и коды ошибок, но не credentials. Batch audit сохраняет IDs и агрегаты, а не секреты сессии. Readiness checks не раскрывают Bot token, `api_hash`, StringSession, TOTP secrets или контакты кандидатов.

## 18. Pilot Certification 1.5

### Защита от обхода масштаба

Pilot stage проверяется не только в SPA. Backend применяет один и тот же gate к preview, ручному запуску, resume, scheduler, staged rollout и непосредственно перед Telegram network call. Production startup запрещён при отключённом `TELEFLOW_PILOT_STAGE_ENFORCEMENT_REQUIRED`.

Assessment для повышения имеет TTL и fingerprint. Повторное использование, пропуск этапа и изменение live-состояния после оценки приводят к отказу. Понижение этапа немедленно приостанавливает несовместимые кампании и задания.

### Конкурентная canary

Canary destination и connection выбираются с транзакционной блокировкой `FOR UPDATE`. Это предотвращает ситуацию, когда два параллельных запроса одновременно проходят один cooldown или дневной лимит. Organization status и emergency stop проверяются до построения gateway. Текст фиксирован сервером, пользовательский body отсутствует, выполняется максимум один transport call и нет autoretry.

Fake transport, fake message ID и локальный режим не считаются доказательством live-доставки. Ошибки `FLOOD_WAIT`, anti-spam, auth revoked и uncertain delivery переводятся в безопасное состояние и требуют ручной проверки.

### Диагностические архивы

Support bundle формируется по allowlist полей. Запрещено включать:

- токены, `api_hash`, StringSession, auth challenges и integration credentials;
- тексты публикаций, шаблонов, диалогов и audit details;
- имена, телефоны, email, usernames, chat IDs и rules URLs;
- cookies, JWT, TOTP и master keys.

UUID заменяются стабильными tenant-scoped псевдонимами. Перед скачиванием проверяется SHA-256, ответ имеет `no-store`, доступ ограничен Owner/Admin, а retention удаляет файл после TTL. Даже обезличенный bundle следует передавать только по согласованному защищённому каналу.
## 19. Commissioning и configuration bundles

Commissioning checks не раскрывают connection strings, токены, usernames групп, содержимое файлов или PII. Ошибки сохраняются только как безопасный `error_type` и агрегированный статус. Контрольный storage-файл создаётся со случайным именем и удаляется в `finally`.

Configuration bundle придерживается deny-by-default схемы:

- credential-like поля запрещены рекурсивным валидатором;
- path traversal, absolute paths, symlinks, encrypted entries и case-insensitive duplicates блокируются;
- manifest обязан перечислять точный набор файлов и их SHA-256;
- import не переносит approval, active permission и runtime state;
- connections создаются без credentials, destinations выключенными/unverified, campaigns draft;
- free-text требует ручной проверки, поскольку пользователь мог случайно вставить секрет в шаблон или статью;
- версия 1.7 добавляет Ed25519-подпись manifest; доверие к автору возникает только после независимой сверки public fingerprint;
- download — `no-store`, удаление уничтожает storage object и фиксируется в audit-chain.

## 20. Artifact Trust security 1.7

### Private key protection

- используется только Ed25519;
- приватная часть хранится как AES-256-GCM ciphertext;
- AAD связывает ciphertext с конкретной DB-записью;
- read schemas/API/UI не содержат `private_key_enc`;
- public export отдаёт только PEM/base64 и fingerprint;
- revoke уничтожает приватный ciphertext и не допускает восстановление через API;
- master-key rotation предварительно расшифровывает все ключи и выполняется транзакционно.

### Signature integrity

Подписываемое сообщение включает domain separator и canonical metadata. Поэтому атакующий не может без приватного ключа изменить:

- artifact purpose;
- key ID;
- fingerprint;
- embedded public key;
- SHA-256 содержимого;
- время создания подписи.

Для каждого artifact type используется отдельный purpose. Подпись configuration bundle не принимается как подпись support bundle или acceptance report.

### Archive verification

До trust evaluation выполняются structural controls:

- лимит размера и количества файлов;
- запрет path traversal и absolute paths;
- запрет symlink и encrypted ZIP entries;
- case-insensitive duplicate detection;
- exact manifest composition;
- SHA-256 каждого payload-файла;
- строгая схема acceptance wrapper/manifest.

### Trust policy

- `optional` предназначен для controlled legacy migration;
- `require_valid` подтверждает владение private key, но не происхождение;
- `require_trusted` требует tenant-local active trust record;
- production startup требует `require_trusted`;
- недействительная и отозванная подпись блокируется при любой policy;
- fingerprint следует сверять по независимому каналу до установки trust.

### Roles and audit

Owner/Admin могут генерировать, импортировать, менять trust/default и отзывать ключ. Viewer может видеть public metadata и проверять файл, но не менять trust store. Все mutation events записываются в audit hash-chain без private material.

### Residual risk

- подпись не шифрует содержимое;
- локальный revoke не является глобальной CRL;
- compromised host/master key может раскрыть активный private key;
- signed timestamp не является внешней trusted timestamp;
- для юридически значимых документов может потребоваться отдельная квалифицированная подпись.

## 21. Recovery Assurance security 1.8

- backup receipt и manifest подписываются разными domain-separated Ed25519 purpose;
- `registered` receipt не считается доказательством доступности архива;
- только локальная проверка artifact SHA-256, manifest и файлов переводит evidence в `verified`;
- production требует age encryption и trusted signature;
- private age identity не принимается API и хранится вне application server;
- ZIP validation блокирует traversal, absolute path, symlink, encrypted entry, duplicate и undeclared file;
- PostgreSQL password передаётся `pg_dump` через `PGPASSWORD`, а не argv;
- raw dump не хранится в web storage;
- restore wrapper не принимает production DSN и выполняет только drill;
- receipt/drill import tenant-bound, RBAC/CSRF protected и записывается в audit-chain;
- failed drill сохраняется как evidence, но не выполняет RPO/RTO;
- corrupt archive не может стать `verified`;
- master key и age private identity должны храниться в независимых escrow.

Остающийся риск: компрометация одновременно database backup, master key и age identity раскрывает application secrets/PII. Компенсирующие меры — разделение доступа, off-site immutable storage, короткий доступ к restore-среде и регулярная ротация.

## Supply Chain Assurance security 2.1

- transparency entries use a domain-separated SHA-256 chain and individual Ed25519 signatures;
- sequence allocation is serialized per organization;
- only active trusted release attestations can be published;
- two different active attestations for one product version are rejected;
- dependency reports are bound to both release payload SHA-256 and server-generated SBOM SHA-256;
- unknown report fields, future timestamps, oversized payloads and excessive findings are rejected before persistence;
- report, SBOM, policy snapshot and attestation digests are immutable evidence;
- verification never silently replaces compromised evidence;
- change management stores the exact assessment ID and never substitutes a later assessment;
- policy changes invalidate existing evidence;
- server TTL is a hard upper bound that cannot be weakened through UI/API;
- production startup requires transparency, vulnerability scan and trusted report gates;
- all mutations require Owner/Admin, CSRF, tenant scope and audit logging.

Residual risk: trust in a signed scanner report still depends on the scanner, its advisory database and the CI host. Use pinned scanner versions, independent CI credentials, external immutable storage and recurring scans.

## Operational SLO security 2.2

- SLO gate повторяется на API, scheduler и pre-network Safety Engine.
- Blocked gate не создаёт Telegram client и не увеличивает счётчик сетевых попыток.
- Policy change инвалидирует старый assessment через SHA-256, а не только через timestamp.
- `waiting_review` считается неопределённой доставкой и расходует error budget.
- Автоматические SLO-инциденты используют стабильный dedup key и не образуют бесконечный поток записей.
- SLO-инциденты исключены из собственной метрики assessment, но учитываются общим gate до явного/автоматического разрешения.
- Viewer не может изменять policy или incident state; close/reopen требуют Owner/Admin.
- Production startup требует `TELEFLOW_SLO_GATE_REQUIRED=true`.

## Execution Fencing security 2.3

### Защищаемое свойство

Для одной организации только worker активной площадки, владеющий актуальным epoch, может начать Telegram network call. Проверка выполняется повторно непосредственно перед сетью, а не только при выборе задания.

### Меры

- уникальный execution lease на организацию;
- `SELECT FOR UPDATE` в PostgreSQL;
- монотонный epoch, не переиспользуемый после takeover;
- TTL lease и heartbeat площадки;
- durable marker `network_started` до удалённого вызова;
- запрет auto-retry после неоднозначного результата;
- независимый Owner/Admin для failover;
- точная подтверждающая фраза;
- audit и critical notification;
- tenant isolation во всех execution API;
- production startup gate.

### Production checklist

- [ ] обе площадки используют одну PostgreSQL-базу;
- [ ] `TELEFLOW_EXECUTION_FENCING_REQUIRED=true`;
- [ ] у площадок разные `TELEFLOW_EXECUTION_SITE_KEY`;
- [ ] `TELEFLOW_EXECUTION_PRIMARY_SITE_KEY` одинаков на всех хостах;
- [ ] NTP синхронизирован;
- [ ] PostgreSQL HA не допускает два writable primary;
- [ ] failover проверен на служебной группе;
- [ ] alerts доставляются операторам;
- [ ] все `network_started/uncertain` попытки сверяются до переключения.

### Остаточный риск

Telegram не поддерживает fencing token и distributed transaction с локальной БД. Падение после принятия сообщения Telegram может оставить неопределённый результат. Безопасная реакция — ручная сверка без автоматической отправки — является частью обязательного workflow.

## Continuity security controls 2.4

- simulation не создаёт Telegram gateway и не меняет lease;
- live-drill переиспользует production failover/fencing;
- target site проверяется по freshness, schema head и runtime fingerprint;
- policy snapshot и runtime snapshot привязываются к drill до запуска;
- каждое событие связано SHA-256 previous hash;
- sign-off повторно проверяет evidence digest, semantic fields и event chain;
- автор не принимает собственное evidence при four-eyes policy;
- live-drill нельзя отменить после переключения без безопасного failback;
- worker synchronization использует отдельный savepoint на drill;
- runtime evidence исключает credentials и приватный ключевой материал;
- production требует continuity gate и независимый sign-off.

## Capacity security controls 2.5

- policy и evidence изолированы по `organization_id`;
- изменение policy разрешено Owner/Admin и защищено CSRF/RBAC;
- ordered limits защищены API, сервисом и DB constraints;
- partial PATCH валидирует итоговую строку целиком;
- admission выполняется до queue mutation;
- staged release выполняется до `held → pending`;
- manual retry проверяется до изменения terminal job;
- confirmed-not-sent проверяет capacity до инвалидизации fencing epoch;
- dispatch precheck выполняется до gateway и attempt counter;
- durable `prepared` reservation закрывает concurrent-worker race;
- отказ финальной reservation сохраняет `telegram_call_started=false`;
- assessment fingerprint не содержит Telegram credentials и message bodies;
- production не запускается при `TELEFLOW_CAPACITY_ASSURANCE_REQUIRED=false`.

Capacity limits не являются способом обойти Telegram FloodWait или anti-spam. Telegram-specific ограничения имеют приоритет и по-прежнему переводят connection/job в безопасное состояние.
