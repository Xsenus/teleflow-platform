# Threat model TeleFlow Platform 2.1

## 1. Активы

- Telegram Bot token;
- MTProto API hash/StringSession;
- Business webhook secrets;
- browser refresh sessions и API keys;
- сообщения и контакты кандидатов;
- automation flows и candidate state;
- AI/Google/integration credentials;
- campaigns, templates, destinations и permission evidence;
- approval requests/decisions/fingerprints и organization stop state;
- operator notifications и audit hash-chain state;
- media, exports, audit и backups;
- encryption master key.

## 2. Trust boundaries

1. Browser/PWA ↔ Nginx/API.
2. Telegram ↔ webhook/API.
3. API/worker ↔ PostgreSQL/Redis/storage.
4. Worker ↔ Telegram API/MTProto.
5. API ↔ optional ClamAV daemon.
6. Worker ↔ AI/integration endpoints.
7. Organization A ↔ Organization B.
8. Host operator ↔ container/service account.

## 3. Основные угрозы

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| Кража operator password | Argon2id, lockout, TOTP, Secure cookies | compromised endpoint/browser |
| Refresh replay/session theft | rotation, hash-only storage, family revoke, session UI | malware before revoke |
| CSRF | SameSite + double-submit token | malicious extension/XSS |
| XSS | escaping, CSP, no CDN | future unsafe UI changes |
| PWA cache leak | static allowlist, no API/auth cache, static offline page | hostile browser profile/extension |
| Tenant data leak | organization filters, scoped API keys, tests | missed filter in new endpoint |
| DB dump disclosure | AES-GCM fields, external master key | metadata/redacted previews remain visible |
| Master key theft | secret manager, host restrictions | encrypted data compromise |
| Telegram session theft | encrypted StringSession, revoke workflow | live process memory/host root |
| Forged Business webhook | path + header secret, constant-time compare | leaked URL and secret |
| Webhook flood | body limit, rate limit, async durable queue | volumetric DDoS upstream |
| Duplicate inbound update | unique update ID | semantic duplicate with new ID |
| Duplicate publication | idempotent jobs, lease, waiting_review | Telegram lacks universal idempotency key |
| Anti-spam restriction | permission evidence, windows, caps, stop-on-flood, global emergency stop | complaints/content policy external |
| Unauthorized destination import | preview, evidence requirement, no auto-join, validation | operator can falsely attest permission |
| Malicious CSV/formula | bounded parser, text treatment, export escaping policy | spreadsheet software behavior after download |
| A/B used to evade filters | stable two-template experiment, approval snapshot | operator-provided prohibited content |
| Unauthorized/implicit campaign launch | explicit approval request, RBAC, fingerprint, run gate | compromised owner/admin account |
| Self-approval conflict | optional four-eyes and independent approver count check | collusion between privileged users |
| Route changed after approval | fingerprint invalidation and pre-send recomputation | bug in future uncovered field |
| Concurrent duplicate decisions | row locks + unique request/user constraint | database/operator misconfiguration |
| Expired approval reused | TTL, proactive worker expiry, current-fingerprint check | stalled worker delays UI status only; send remains blocked |
| Emergency stop race | organization row state, scheduler skip, pre-gateway safety check | Telegram call already completed before stop transaction |
| Notification storm | dedup key/window and severity state | different event keys may still be numerous |
| Audit record alteration | SHA-256 chain + stored head + verify/backfill | DB administrator can rewrite rows and chain together; external WORM needed |
| Audit chain state deletion | verification treats missing state as failure | attacker can also suppress monitoring |
| Failed key rotation | preflight, dry-run, DB transaction, storage rollback journal, backup | external storage cannot be crash-atomic with DB |
| Broken flow graph | schema/graph validation, immutable active revision | logic still may be semantically poor |
| Prompt injection | untrusted-data framing, detection, strict JSON | model may still misclassify |
| AI data leakage | tenant context, redaction, provider policy | external provider processing |
| SSRF | URL/DNS/IP validation, HTTPS | DNS rebinding/allowed endpoint compromise |
| Malicious upload | size/MIME/signature checks + optional ClamAV | zero-day/archive formats; scanner disabled |
| ClamAV outage/bypass | fail-closed option, audit | fail-open risk acceptance |
| Outbox duplicate | per-endpoint state and event ID | crash around remote commit |
| Backup theft | age encryption, checksum, off-site ACL | key-management failure |
| Worker split brain | DB lease + Redis singleton locks | misconfigured shared MTProto session |

## 4. Abuse cases intentionally excluded

- auto-join Telegram groups;
- unsolicited mass DMs;
- scraping participants for outreach;
- account/device/proxy rotation after restrictions;
- randomization to evade moderation;
- automatic continuation after anti-spam warning;
- automatic hire/reject decision by AI;
- browser/PWA offline storage of conversations or contacts.

## 5. Security verification

Automated tests cover:

- CSRF/RBAC/refresh replay/TOTP lockout;
- active session list/revoke;
- tenant isolation;
- AES-GCM tamper detection;
- webhook secret/body limit/deduplication;
- prompt injection redaction;
- SSRF guards;
- flow graph validation and encrypted contact collection;
- bulk import constraints and no-auto-join behavior;
- destination windows/cooldown;
- ClamAV clean/malware/outage policies;
- PWA no-sensitive-cache rules;
- outbox per-endpoint retry semantics;
- retention/privacy;
- production configuration rejection;
- explicit/four-eyes/multi-approval and stale fingerprint rejection;
- emergency stop before gateway call;
- notification dedup/read/ack;
- audit tamper, backfill and missing-state detection;
- master-key dry-run/rotation and weak-key rejection.

Live penetration testing, dependency scanning, host hardening audit, ClamAV signature maintenance and Telegram credentials remain deployment-specific gates.

## 6. Дополнительные угрозы Controlled Operations 1.3

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| Разрешение группы истекло между approval и отправкой | свежий scheduler preflight + delivery-time expiry check | правила могут измениться до формальной даты; нужна ручная ревизия |
| Массовая ошибка сразу по всему маршруту | физически удерживаемые staged batches и checkpoint | первый пакет всё равно требует наблюдения оператора |
| Повтор после сетевого разрыва создаёт дубль | `waiting_review`, запрет auto-retry, ручная reconciliation | оператор может ошибиться при визуальной проверке |
| Повтор одинакового объявления в cooldown-период | destination-scoped content fingerprint guard | семантически одинаковый, но изменённый текст не считается идентичным |
| Обход held через API retry/resume | статус не разрешён в retry/resume и не выбирается worker | привилегированный DBA способен менять БД; audit/infra controls обязательны |
| Подмена checkpoint результата | RBAC, row lock, audit hash-chain, note/reviewer/time | компрометация Owner/Admin требует incident response |

## 7. Дополнительные угрозы Production Pilot 1.4

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| Публикация после отзыва права писать | Validation TTL, manual/batch recheck, `write_forbidden`, Safety block | Право может быть отозвано между проверкой и отправкой |
| Запуск во время обслуживания или запрещённого периода | Organization/connection/destination blackout before gateway | Неверно настроенная timezone или неполный календарь |
| Использование старого readiness после изменения кампании | Fingerprint recheck and TTL | Изменение внешних правил группы не входит в fingerprint автоматически |
| Массовая revalidation создаёт FloodWait | Последовательный batch, max 50, stop/defer after retry-after | Telegram может ограничить раньше опубликованного/ожидаемого порога |
| Cross-tenant blackout/reference manipulation | Tenant-scoped lookup and foreign-reference validation | Ошибка администратора внутри собственной организации |
| Подмена validation history | Audit event plus DB access controls; audit hash-chain for actions | Таблица history сама по себе не является внешним immutable ledger |
| Ложное ощущение «безопасного запуска» | UI/documentation distinguishes local readiness from Telegram permission/anti-spam outcome | Оператор может игнорировать warnings или правила группы |

## 16. Дополнения threat model 1.5

| Угроза | Возможный путь | Контроль |
|---|---|---|
| Обход pilot stage | старый UI, прямой API, scheduler или уже ожидающий job | единый server-side gate во всех путях и финальная проверка Safety Engine до gateway |
| Повторное использование оценки | replay старого assessment после изменения состояния | TTL, requested/current stage, consumed state и fingerprint |
| Параллельные canary обходят cooldown | два одновременных HTTP-запроса | DB row locks connection+destination, caps внутри транзакции, один transport call |
| Canary превращается в рекламный sender | произвольный body/media из запроса | серверный фиксированный текст, нет media и autoretry |
| Canary обходит emergency stop | отдельный служебный endpoint не использует обычный delivery path | organization status/publishing switch проверяются до gateway |
| Fake evidence повышает live stage | `fake-*` message ID или fake mode | отдельный `is_fake`, fake delivery исключена из evidence |
| Support bundle раскрывает секреты | сериализация моделей/логов целиком | allowlist DTO, pseudonymization, regression tests, internal manifest, TTL |
| Подмена support bundle в storage | замена файла после генерации | сохранённый SHA-256 и verification перед download |
| Несанкционированное скачивание диагностики | Operator/Viewer или другой tenant | Owner/Admin RBAC, tenant-filtered lookup, CSRF для create/delete, audit |
| Понижение этапа не останавливает очередь | старые jobs продолжают работу | transactionally pause incompatible campaigns/jobs + Safety recheck |

Остаточный риск: обезличенные operational metadata могут раскрывать объём и структуру системы. Поэтому bundle не публикуется открыто и автоматически удаляется. Pilot certification снижает вероятность ошибочного масштабирования, но не гарантирует отсутствие Telegram-ограничений или жалоб.
## 13. Новые угрозы 1.6

### Поддельный переносимый архив

Угроза: атакующий изменяет ZIP или добавляет credential-like поля.

Меры: строгий allowlist schema v1, canonical manifest, SHA-256 каждого файла, запрет лишних entries, secret-field scanner, лимиты размера/количества и fail-safe imported state. Manifest не считается электронной подписью; источник архива должен проверяться внешним доверенным каналом.

### Автоматический запуск после импорта

Угроза: импортированная конфигурация начинает публикации без повторной авторизации и разрешений.

Меры: connection=`draft`, destination disabled/unverified, campaign=`draft`, automation/integration/blackout disabled, approvals и jobs не экспортируются.

### Подмена evidence пилота

Угроза: оператор изменяет результат campaign run перед sign-off.

Меры: immutable evidence snapshot, SHA-256, context binding к program/stage/campaign/run, audit-chain и отдельный Owner/Admin sign-off для live stage.

## 14. Новые угрозы 1.7 — Artifact Trust

| Угроза | Возможный путь | Контроль | Остаточный риск |
|---|---|---|---|
| Подмена payload и пересчёт обычного SHA-256 | атакующий меняет ZIP и manifest | Ed25519-подпись canonical manifest/payload | компрометация private key |
| Перенос подписи между типами файлов | signature configuration bundle прикладывается к support bundle | отдельный signed `purpose` и domain separation | ошибка будущего verifier при несовместимом schema upgrade |
| Подмена signature metadata | изменение key ID, fingerprint, времени или public key | все metadata входят в подписываемое сообщение | signed timestamp не внешне удостоверен |
| Доверие неизвестному signer | пользователь принимает embedded public key без сверки | `valid_untrusted`, explicit trust, `require_trusted`, fingerprint ceremony | оператор может неверно сверить fingerprint |
| Использование отозванного ключа | старый private key подписывает новый файл | tenant revoke status, policy block, private ciphertext destruction | внешний verifier без локального revoke list видит математически корректную подпись |
| Утечка приватной части через API/UI | ORM сериализация или debug output | отдельные schemas, no private field frontend, regression/static tests | privileged DB/host compromise |
| Два параллельных default keys | concurrent generate/default requests | organization row lock и транзакционный reset | ручное изменение БД вне приложения |
| Подмена acceptance manifest | изменение algorithm/generated_at | strict manifest field set, SHA-256 algorithm, timestamp equality | внешняя юридическая сила не создаётся автоматически |
| ZIP parser abuse | traversal, symlink, duplicates, encrypted entries, zip bomb | safe path, file count, uncompressed size, exact manifest | очень сложные parser bugs в стандартной библиотеке |
| Утеря ключа при revoke | ошибочный административный отзыв | explicit privileged action, audit, reason, public history | private part удаляется необратимо; нужен новый key rollout |

Artifact Trust снижает риск незаметной подмены переносимых файлов, но не заменяет host security, защищённый канал первичной сверки fingerprint и резервное хранение публичных ключей.

## 15. Новые угрозы 1.8 — Recovery Assurance

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| Подмена backup archive | SHA-256 receipt + signed manifest + per-file hashes | Компрометация trusted private signer |
| Подмена только receipt | Ed25519 purpose-bound signature и tenant binding | Неверно доверенный public key |
| Path traversal/symlink в ZIP | Строгая проверка до extraction | Ошибка стороннего decompressor вне платформы |
| Receipt без доступного backup выдаётся за RPO | Состояние `registered`; RPO считает только `verified` | Оператор может неверно интерпретировать внешний файл вне UI |
| Drill изменяет production | Веб restore отсутствует; wrapper drill-only; disposable path | Ручная внешняя команда с неверным DSN |
| Утечка database dump через API | API принимает только малые JSON receipts | Администратор может передать файл иным каналом |
| Утечка DB password в process list | `PGPASSWORD` вместо URL в argv | Environment может читаться root-пользователем |
| Потеря age identity | Offline escrow и регулярный drill | Полная необратимость encrypted archive |
| S3 не попал в local backup | Явный storage claim/warning | Неполный recovery без bucket replication |
| Ложный PostgreSQL restore success | Режим `metadata_only`, не `postgres_isolated` | Полный внешний drill всё ещё операционный процесс |

## Угрозы Supply Chain Assurance 2.1

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| Подмена опубликованного release | signed transparency hash-chain, attestation digest/version/commit verification | локальный журнал не является публичным timestamp service |
| Выбор более нового «удобного» scan после approval | change request хранит точный assessment ID | привилегированный DBA требует инфраструктурного контроля и внешнего audit export |
| Подмена report/SBOM/policy snapshot | отдельные SHA-256, signature verification, immutable evidence | компрометация signing key/host требует revoke и incident response |
| Inventory-only выдаётся за CVE scan | явный `kind`, production требует `vulnerability_scan` | качество scanner database остаётся внешним фактором |
| Устаревший scan продолжает разрешать upgrade | TTL, server hard cap, policy SHA и повторная verification | advisory может появиться внутри допустимого TTL |
| Уязвимость скрыта неизвестным полем | строгая allowlist-схема report | scanner adapter должен корректно нормализовать исходный формат |
| Cross-tenant reuse evidence | organization filters и FK validation | DBA-level compromise вне application threat boundary |
| DoS большим отчётом | byte limit, 5 000 findings, bounded strings | допустимый верхний предел всё ещё расходует CPU на canonicalization |

## Threats covered by Operational SLO 2.2

| Угроза | Контроль |
|---|---|
| Запуск через альтернативный API при плохом состоянии | единый gate в API, scheduler и Safety Engine |
| Использование просроченной оценки | TTL + policy SHA-256 + `assessment_is_current` |
| Скрытие неопределённой доставки | `waiting_review` входит в failures/error budget |
| Подмена результата оценки | immutable assessment и SHA-256 fingerprint |
| Шторм одинаковых инцидентов | stable dedup key `slo:<check_code>` |
| Самоподдерживающийся SLO-инцидент | SLO-source исключён из assessment critical-incident metric |
| Несанкционированное закрытие инцидента | RBAC Owner/Admin и audit-chain |
| Ослабление production gate конфигурацией | runtime startup validation и production Compose override |

Оставшийся риск: SLO отражает только доступные платформе метрики и не доказывает отсутствие Telegram moderation actions или пользовательских жалоб. Он не используется как механизм обхода ограничений.

## Threats covered by Execution Fencing 2.3

### T-EF-1: split-brain workers

**Сценарий:** два worker считают себя active и одновременно отправляют одно задание.
**Защита:** tenant lease, row lock, holder worker и epoch; standby scheduler не создаёт jobs.
**Остаток:** корректность зависит от единой authoritative PostgreSQL.

### T-EF-2: stale worker после failover

**Сценарий:** старый процесс оживает после переключения.
**Защита:** failover увеличивает epoch; старый epoch блокируется Safety Engine и network fence.

### T-EF-3: crash после удалённого принятия

**Сценарий:** Telegram принял сообщение, процесс умер до локального commit.
**Защита:** durable `network_started`, stale recovery в `waiting_review`, отсутствие auto-retry.
**Остаток:** требуется ручная проверка чата.

### T-EF-4: несанкционированный failover

**Сценарий:** один скомпрометированный администратор переключает площадку.
**Защита:** RBAC, независимый Owner/Admin, точная фраза, blockers, audit-chain, notification.

### T-EF-5: failover для обхода Telegram restriction

**Сценарий:** оператор пытается сменить площадку после FloodWait/anti-spam.
**Защита:** ограничения принадлежат Telegram connection и сохраняются в общей БД; failover не очищает restriction и не меняет credentials.

### T-EF-6: clock drift

**Сценарий:** разные часы ошибочно определяют expiry.
**Защита:** короткий lease, heartbeat TTL, operational monitoring; production требует NTP.
**Остаток:** значительный drift может снижать доступность, поэтому fail-safe блокирует сеть.

## Continuity threats 2.4

| Угроза | Контроль |
|---|---|
| Split-brain во время учения | Один execution lease, row lock и монотонный epoch |
| Старый worker продолжает Telegram-вызовы | Fencing epoch проверяется перед сетью |
| Standby работает на другом релизе | Runtime compatibility по version/revision/config/release digest |
| Подмена evidence | Canonical SHA-256 плюс semantic comparison с БД |
| Удаление или перестановка событий | Hash-linked sequence с tenant binding |
| Самостоятельная приёмка | Distinct Owner/Admin sign-off |
| Simulation случайно обращается к Telegram | Отдельная zero-network ветка и regression test |
| Ошибка одного drill откатывает остальные | Per-drill database savepoint |
| Отмена оставляет standby active | Запрет cancel до failback |
| Failover используется для обхода Telegram restriction | Общие Safety/SLO/Telegram connection gates остаются обязательными |

## Capacity & Backpressure threats 2.5

| Угроза | Контроль |
|---|---|
| Крупная кампания мгновенно создаёт чрезмерную очередь | Projected admission до CampaignRun/DeliveryJob и повторная транзакционная проверка |
| Два workers одновременно проходят rate check | Durable `prepared` reservations и lock CapacityPolicy |
| Почти заполненная ready queue скрыта большим active limit | Utilization берёт максимум независимых измерений |
| Частичный PATCH создаёт противоречивую policy | Валидация effective row и DB constraints |
| Retry изменяет terminal job до отказа | Capacity check под row lock до mutation |
| Старый assessment используется как разрешение | Dispatch всегда пересчитывается по текущим строкам |
| Временный rate-window навсегда блокирует новые bounded jobs | Rate blockers отделены от structural admission blockers; dispatch остаётся deferred |
| Failed reservation считается Telegram-вызовом | `network_started_at` отсутствует, attempt `abandoned`, gateway не создаётся |
| Tenant видит или изменяет policy другого tenant | Organization-scoped queries и RBAC tests |
| Оператор отключает gate в production | Runtime security validation и production Compose override |

Остаточный риск: фактическая производительность PostgreSQL, Redis, сети и Telegram зависит от инфраструктуры. Она подтверждается load-test и canary, а не только unit/integration тестами.
