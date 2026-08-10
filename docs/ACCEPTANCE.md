# Критерии приёмки TeleFlow Platform — история 1.x–2.5

## 1. Базовый кодовый релиз 2.3 (исторические evidence)

- [x] приложение запускается после Alembic migration `9b3d5f7a1c4e`;
- [x] 246 automated tests проходят;
- [x] statement coverage 79,83% при gate не ниже 65%;
- [x] Python и JavaScript syntax checks проходят;
- [x] release asset validator проверяет 2.3 files, UI, execution fencing, SLO assets и production flags;
- [x] migration round-trip 2.3 → 2.2 → 2.3 проходит;
- [x] HTTP login/CSRF/dashboard/logout smoke проходит;
- [x] worker once, audit-chain и master-key dry-run проходят;
- [x] signed recovery backup/verify/isolated drill проходят;
- [x] transparency chain, CycloneDX SBOM, dependency assessment и Operational SLO gates покрыты тестами;
- [x] архив не содержит credentials, sessions, databases и private keys.

## 2. Auth/RBAC и browser sessions

- viewer не меняет данные;
- operator создаёт content/drafts и может запросить approval, но не принимает решение и не управляет secrets;
- admin управляет connections/campaigns/automation;
- owner управляет users/API keys;
- изменяющий cookie request без CSRF получает 403;
- повтор refresh token блокируется;
- production требует TOTP owner/admin;
- пользователь видит только собственные browser sessions;
- selective revoke завершает выбранное устройство;
- revoke-others сохраняет только текущую session;
- отзыв текущей session завершает доступ.

## 3. Tenant isolation

- объект другой организации не раскрывается по UUID;
- API key видит только свою организацию;
- caps, analytics, queue, audit, privacy и outbox не смешиваются;
- migration назначает legacy data в bootstrap organization.

## 4. Destinations

### Разрешения

- destination без permission evidence остаётся unverified;
- unverified destination блокирует approval;
- denied/write-forbidden destination отключается;
- topic ID сохраняется и передаётся gateway;
- discovery не подтверждает разрешение автоматически.

### Bulk import/export

- TXT/CSV/TSV preview не меняет БД;
- invite links отклоняются;
- auto-join отсутствует;
- duplicate получает отдельный статус;
- конкурентный duplicate не повреждает transaction;
- `permission_confirmed=true` требует evidence;
- export формирует корректный CSV.

### Individual schedule

- IANA timezone валидируется;
- weekdays применяются в local timezone;
- обычное и ночное time window рассчитываются корректно;
- worker переносит `due_at` до ближайшего разрешённого времени;
- per-destination cooldown применяется после успешной отправки.

## 5. Media security

- MIME/extension/signature mismatch отклоняется;
- path traversal невозможен;
- oversize file отклоняется;
- clean file проходит при доступном ClamAV;
- malware result блокируется до storage и записывается в audit;
- scanner outage при fail-closed возвращает 503;
- fail-open допускается только при явной настройке и фиксируется в audit.

## 6. Campaigns и publisher

### Campaign lifecycle

- preview показывает порядок, время, text, media, blockers/warnings;
- запуск требует отдельного актуального approval request;
- утверждение связано с SHA-256 fingerprint защищённой конфигурации;
- draft/paused campaign можно редактировать;
- изменение route/schedule/template снимает approval;
- copy создаёт независимый draft;
- pause/resume/cancel влияют на due jobs;
- повторный scheduler cycle не создаёт duplicate;
- stale lease восстанавливается.

### Governance и approvals

- `run-now` без approved/current fingerprint получает 409;
- normal campaign требует минимум одно уникальное owner/admin решение;
- four-eyes блокирует решение автора запроса;
- high-risk threshold требует настроенное число независимых решений;
- при недостатке approvers запрос не создаётся;
- повторное решение того же пользователя отклоняется;
- request TTL истекает проактивно в worker и создаёт audit/notification;
- изменение template/body/media/route/schedule/permission/window/cooldown отменяет request;
- concurrent decisions сериализуются и не превышают требуемое число;
- resume paused campaign также требует current approval.

### Organization emergency stop

- pause доступен owner/admin и требует причину;
- scheduler не создаёт новые runs для paused organization;
- pending/retry jobs переходят в `waiting_review` с кодом stop;
- уже арендованный job блокируется Safety Engine до gateway call;
- resume является отдельным audit action;
- возобновлённые jobs повторно проходят approval/permission/caps/windows;
- UI показывает stop state во всех разделах.

### Notifications

- unread/read/acknowledged разделены;
- critical event требует явного acknowledgement;
- одинаковые события коалесцируются по dedup window;
- occurrence count и last occurrence обновляются;
- чужая организация не читает и не подтверждает notification;
- создание notification формирует outbox event.

### A/B templates

- secondary template не может совпадать с primary;
- weight без secondary template отклоняется;
- weight 1–99 валидируется;
- assignment стабилен для campaign/destination;
- preview и job snapshot показывают вариант A/B;
- редактирование template после approval не меняет существующий job;
- analytics разделяет delivery по variants;
- delivery success не выдаётся за candidate conversion без attribution.

### Telegram errors

- SlowMode откладывает только destination;
- FloodWait ставит connection на pause/manual review;
- anti-spam не имеет autoretry;
- uncertain result переводит job в `waiting_review`;
- retry возможен только после ручной проверки.

## 7. Telegram Business

- webhook URL HTTPS в production;
- неверный path/header secret отклоняется;
- oversized body получает 413;
- duplicate `update_id` не создаёт вторую запись;
- webhook отвечает до AI processing;
- business connection update создаёт или обновляет state;
- edit/delete updates синхронизируют conversation records.

## 8. Visual flows, consent, AI и candidates

- до consent automation не отвечает при `require_consent_before_ai=true`;
- positive consent включает qualification;
- decline/stop отключает automation;
- handoff keyword назначает human flow;
- дневной лимит автоответов соблюдается;
- graph с cycle/missing transition/unreachable node отклоняется;
- active flow revision read-only;
- message/question/choice/handoff/end выполняются детерминированно;
- age/phone/email validation работает;
- drag-and-drop меняет порядок DOM, кнопки up/down остаются fallback;
- phone/email сохраняются только encrypted;
- rule-based provider собирает обязательные поля;
- OpenAI-compatible response проходит JSON validation;
- AI не выставляет финальный hire/reject verdict;
- contact endpoint требует отдельное право/scope.

## 9. Analytics

- range ограничен current organization;
- timezone используется для daily buckets;
- delivery/conversation/candidate/AI/outbox aggregates корректны;
- funnel conversion считается от предыдущего stage;
- A/B breakdown использует snapshot variant;
- CSV не содержит body, phone или email;
- пользователь другой организации не видит aggregates.

## 10. Integrations

- webhook signature проверяется получателем;
- endpoint получает только subscribed event types;
- test event идёт только выбранному endpoint;
- после частичной ошибки успешный endpoint не вызывается повторно;
- исчерпанный retry переводит event в `dead`;
- CSV создаётся отдельно на event;
- private/localhost URL отклоняется в production.

## 11. Privacy

- export формируется только для subject текущей организации;
- download имеет TTL;
- delete scrub-ит messages/contacts/identifiers;
- retention удаляет raw update body;
- audit сохраняет факт операции без удалённого PII.

## 12. PWA

- manifest и icons обслуживаются;
- service worker устанавливается только в secure context/localhost;
- API, auth, webhook, metrics и non-GET не кэшируются;
- offline page статична и не содержит user data;
- отсутствие PWA не ломает browser/API operation.

## 13. Audit integrity и master key

- новая audit запись получает sequence/prev_hash/entry_hash/version;
- verify проходит для неизменённой цепи;
- изменение защищённого поля обнаруживается;
- удаление chain state обнаруживается;
- offline backfill связывает legacy rows только при остановленных writers;
- system chain проверяется CLI;
- key-rotation dry-run расшифровывает все поддерживаемые encrypted fields без изменения;
- слабый/невалидный новый key отклоняется;
- реальная rotation повторно шифрует DB fields и encrypted privacy objects;
- plaintext secret не попадает в output/audit;
- запуск со старым env key после rotation должен быть предотвращён процедурой deployment.

## 14. Production deployment

- PostgreSQL/Redis healthy;
- migration service завершён;
- API live/ready healthy;
- worker heartbeat свежий;
- TLS/HSTS включены;
- `/metrics` закрыт снаружи;
- owner password изменён и TOTP включён;
- active sessions проверены;
- backup encrypted/off-site;
- restore drill выполнен;
- при public upload ClamAV fail-closed проверен;
- alerts поступают ответственному;
- governance policy выполнима текущим числом owner/admin;
- emergency stop проверен в fake mode;
- audit backfill/verify завершены после upgrade.

## 15. Внешняя Telegram-приёмка

Релиз считается введённым в эксплуатацию после выполнения `PILOT_CHECKLIST.md` и фиксации:

- Bot identity;
- MTProto identity;
- Business connection ID;
- пяти destinations с подтверждённым разрешением;
- Telegram message IDs;
- update IDs;
- фактических прав и schedule;
- отсутствия дублей и несанкционированных получателей;
- корректной остановки при контролируемом permission revoke;
- успешного backup restore drill.

## 16. Controlled Operations 1.3

- [x] Destination хранит `permission_reviewed_at`/`permission_expires_at`; истёкшее разрешение блокируется на трёх уровнях.
- [x] Preflight сохраняется, tenant-isolated, содержит fingerprint/items/summary/TTL и не заменяет delivery safety.
- [x] Scheduler всегда создаёт свежий preflight перед run.
- [x] Duplicate guard не вызывает Telegram gateway и фиксирует `DUPLICATE_CONTENT`.
- [x] В staged mode только первый пакет доступен worker, остальные имеют `held`.
- [x] Checkpoint раскрывает ровно следующий пакет; auto-release соблюдает threshold и блокируется `waiting_review`.
- [x] Abort запрещён при `processing`, отменяет остаток, ставит кампанию на паузу и снимает approval.
- [x] `DELIVERY_RESULT_UNCERTAIN` не доступен обычному retry/cancel/resume, блокирует campaign cancel/staged abort и сохраняется при connection revoke.
- [x] Reconciliation поддерживает `confirmed_sent`, `confirmed_not_sent`, `skipped` с RBAC/audit/notification.
- [x] UI показывает preflight, batch, checkpoint и review fields без раскрытия секретов.
- [x] Alembic fresh upgrade/check и round-trip 1.3→1.2→1.3 проходят.

## 17. Production Pilot 1.4

### Destination validation

- [x] successful create/import stores validation timestamp and history record;
- [x] TTL expiry appears in Pilot attention and blocks Safety Engine;
- [x] batch accepts no more than 50 unique IDs;
- [x] retry-after defers remaining batch items without new gateway calls;
- [x] `CHAT_WRITE_FORBIDDEN` becomes `write_forbidden` and disables destination;
- [x] history is tenant-isolated and does not expose credentials;
- [x] custom display title survives initial bulk/create validation.

### Blackout calendar

- [x] one-time organization/connection/destination rules are enforced;
- [x] weekly rule honors IANA timezone and weekday;
- [x] overnight interval remains active after midnight until end time;
- [x] overlapping matches use the latest end as `defer_until`;
- [x] gateway is not constructed and attempt count remains unchanged;
- [x] PATCH scope/kind removes stale incompatible fields;
- [x] all create/update/delete actions are audited.

### Readiness

- [x] report persists structured checks, blockers, warnings, fingerprint and expiry;
- [x] blocked report cannot authorize a launch;
- [x] expired report cannot authorize a launch;
- [x] campaign mutation invalidates the old report by fingerprint;
- [x] `run-now` and `resume` enforce policy when enabled;
- [x] scheduler pauses a due campaign without current readiness;
- [x] scheduler creates critical audit and notification events;
- [x] fake mode is explicitly shown as warning, not live Telegram proof;
- [x] Production configuration refuses startup with readiness gate disabled.

### Release

- [x] Alembic head is `c9e1f3a5b7d9`;
- [x] downgrade to `b7c9d2e4f6a8` and re-upgrade pass;
- [x] all 121 collected tests pass in isolated shards;
- [x] coverage remains at least 65%;
- [x] clean archive passes manifest and forbidden-file checks;
- [x] external live-gates are not marked as completed without credentials.

## Acceptance criteria 1.5 — Pilot Certification & Diagnostics

Релиз принимается по коду при выполнении всех условий:

- новые организации стартуют на `LOCAL`, мигрированные сохраняют совместимый `HUNDRED`;
- preview/run/resume/scheduler/staged/Safety блокируют маршрут выше текущего stage;
- assessment создаётся только для следующего этапа, имеет TTL/fingerprint и не используется повторно;
- fake canary и fake delivery не считаются live evidence;
- canary имеет фиксированный server-side body, один transport call и DB locking;
- понижение этапа приостанавливает несовместимые кампании и jobs;
- support bundle исключает secrets, PII, message bodies, chat IDs/usernames/rules URLs;
- download проверяет SHA-256 и отдаётся с `Cache-Control: no-store`;
- Owner/Admin имеют write-доступ, Operator/Viewer — только разрешённый read;
- migration 1.4→1.5, downgrade 1.5→1.4 и повторный upgrade проходят;
- release QA, manifest и forbidden-file scan проходят на распакованном ZIP.

Live-приёмка отдельно требует настоящего Telegram-подключения, собственной служебной группы и подтверждённых назначений. Автоматические тесты не заменяют фактическую проверку доставки.

## Acceptance criteria 1.6 — Commissioning & Portability

Релиз принимается по коду при выполнении всех условий:

- commissioning report сохраняет checks, blockers, warnings, TTL и SHA-256 fingerprint;
- blocked/expired commissioning report не допускает live-этап формальной программы пилота;
- live-размеры ограничены только `1/5/20/50/100`, уникальны, возрастают и начинаются с 1;
- этап нельзя начать до принятия предыдущего этапа и без точного количества активных назначений;
- local-этап требует fake mode, live-этапы требуют отключённый fake mode;
- evidence привязывается к конкретному завершённому campaign run и защищено SHA-256;
- failed/cancelled/waiting_review или неполная доставка блокируют sign-off;
- при включённом distinct sign-off инициатор этапа не принимает собственный live-этап;
- акт приёмки повторно проверяет evidence integrity и имеет собственный payload SHA-256;
- configuration export не содержит credentials, secrets, approvals и runtime jobs;
- preview/import отклоняют path traversal, symlink, encrypted/duplicate entries, manifest mismatch, незаявленные файлы и secret-like fields;
- импорт создаёт connections без credentials, destinations disabled/unverified, campaigns draft и automations disabled;
- переносимые media проходят повторную MIME/magic/SHA-256/ClamAV policy-проверку;
- download доступен только Owner/Admin, имеет `Cache-Control: no-store` и проверку SHA-256;
- delete уничтожает storage object, удаляет DB row и фиксируется в audit-chain;
- migration 1.5→1.6, downgrade 1.6→1.5 и повторный upgrade проходят;
- release QA, manifest и forbidden-file scan проходят на чистом распакованном ZIP.

Live-приёмка отдельно требует настоящего Telegram-подключения, служебной группы, подтверждённых назначений и прохождения commissioning без blockers. В 1.7 configuration bundle должен иметь Ed25519-подпись доверенного signer; fingerprint всё равно сверяется по независимому каналу.

## Acceptance criteria 1.7 — Artifact Trust & Signed Evidence

### Key lifecycle

- [x] Owner/Admin может создать Ed25519 key;
- [x] API/UI не возвращают private key или `private_key_enc`;
- [x] public PEM/base64 и fingerprint экспортируются;
- [x] imported public key не может быть default signer;
- [x] default key имеет локальную private part;
- [x] concurrent default transition сериализуется organization lock;
- [x] revoke снимает trust/default и уничтожает private ciphertext;
- [x] master-key rotation повторно шифрует active private key.

### Signed artifacts

- [x] configuration bundle содержит `SIGNATURE.json`;
- [x] support bundle содержит `SIGNATURE.json`;
- [x] pilot acceptance report содержит signature envelope;
- [x] изменение payload обнаруживается;
- [x] пересчёт manifest без private key обнаруживается;
- [x] изменение signature metadata обнаруживается;
- [x] перенос подписи между artifact purposes блокируется;
- [x] acceptance manifest принимает только SHA-256 и согласованное время;
- [x] revoked signer блокируется;
- [x] cross-tenant import требует explicit trust при production policy.

### Policy and operations

- [x] `optional`, `require_valid`, `require_trusted` различаются;
- [x] production runtime требует `require_trusted`;
- [x] commissioning проверяет default/trusted signing key;
- [x] автономный CLI проверяет ZIP и JSON;
- [x] UI содержит 24-й раздел «Подписи и доверие»;
- [x] Viewer не может менять key registry;
- [x] audit фиксирует generate/import/update/default/revoke без private material;
- [x] migration 1.6↔1.7 проходит round-trip;
- [ ] fingerprint публичного ключа сверен между двумя реальными инсталляциями;
- [ ] signed configuration bundle перенесён между двумя реальными серверами;
- [ ] incident drill с revoke/new-key rollout выполнен владельцем.

Последние три пункта являются deployment acceptance и не могут быть закрыты без второй инсталляции и операционного владельца.

## Acceptance criteria 1.8 — Recovery Assurance & Continuity

- [x] recovery policy хранит RPO, RTO, drill age и retained count;
- [x] receipt import создаёт `registered`, а не ложный `verified`;
- [x] только полная проверка archive/manifest/files/database переводит backup в `verified`;
- [x] corrupt/tampered archive не удовлетворяет RPO;
- [x] backup receipt и manifest подписываются разными Ed25519 purpose;
- [x] future timestamps и cross-tenant receipts отклоняются;
- [x] ZIP traversal, absolute paths, symlink, duplicate и undeclared entries блокируются;
- [x] SQLite restore выполняется в изолированной copy;
- [x] PostgreSQL metadata check не называется full restore;
- [x] failed drill сохраняется как signed evidence, но не выполняет RTO;
- [x] web API не принимает raw backup и age identity;
- [x] production нельзя запустить без encrypted backup/trusted signature/age recipient;
- [x] commissioning включает recovery assurance;
- [x] `restore.sh` является non-destructive drill wrapper;
- [x] migration 1.7↔1.8 проходит round-trip;
- [ ] реальный PostgreSQL disposable restore drill — deployment gate;
- [ ] off-site immutable retention — infrastructure gate;
- [ ] production age key escrow ceremony — owner gate.

## Acceptance criteria 2.1 — Release Transparency & Dependency Assurance

### Transparency

- [x] publish/withdraw events tenant-scoped;
- [x] sequence уникален и сериализуется по организации;
- [x] previous hash и entry hash проверяются;
- [x] каждая entry подписана отдельным Ed25519 purpose;
- [x] изменение entry или release attestation обнаруживается;
- [x] второй активный attestation той же версии блокируется;
- [x] revoke требует причины;
- [x] chain verification доступна read-only ролям.

### Dependency evidence

- [x] CycloneDX 1.6 SBOM детерминирован;
- [x] report связан с attestation и SBOM hashes;
- [x] inventory-only не считается vulnerability scan;
- [x] signature/trust, severity, exact pins, prerelease, denied packages и TTL проверяются;
- [x] report size и findings bounded;
- [x] report/SBOM/policy/attestation hashes неизменяемы;
- [x] policy change требует новый assessment;
- [x] web TTL не превышает server hard cap;
- [x] cross-tenant и cross-attestation evidence блокируются.

### Change management and release engineering

- [x] change request хранит exact assessment ID;
- [x] verification не выбирает latest assessment;
- [x] production runtime требует transparency/scan/trusted report;
- [x] Alembic 2.0↔2.1 round-trip поддержан;
- [x] UI/API/docs/release validator включают supply-chain module;
- [ ] реальный vulnerability scanner подключён владельцем;
- [ ] transparency log экспортируется во внешнее immutable storage;
- [ ] production signing-key/scanner incident drill выполнен.

## Operational SLO & Incident Assurance 2.2

- [x] SLO policy изолирована по организации;
- [x] assessment сохраняет window, snapshot, policy hash, metrics, checks, TTL и fingerprint;
- [x] failed и waiting_review расходуют error budget;
- [x] отсутствующий/устаревший heartbeat блокирует assessment;
- [x] изменение policy делает старую оценку неактуальной;
- [x] run-now/resume блокируются без актуальной оценки при включённом gate;
- [x] scheduler повторяет gate;
- [x] Safety Engine повторяет gate до Telegram network call;
- [x] change pre/post checks включают `operational_slo`;
- [x] commissioning показывает SLO status;
- [x] auto incident deduplicates по check code;
- [x] восстановление показателя создаёт отдельное resolved event;
- [x] lifecycle и RBAC инцидентов покрыты тестами;
- [x] production rejects disabled `TELEFLOW_SLO_GATE_REQUIRED`;
- [x] Prometheus, alerts и Grafana assets валидны;
- [ ] live thresholds приняты владельцем после реального пилота;
- [ ] реальные error-budget данные проверены на production PostgreSQL/Redis/Telegram.

## Execution Fencing & Failover Assurance 2.3

- [x] существует один tenant execution lease;
- [x] takeover увеличивает epoch;
- [x] живой чужой holder блокирует второй worker;
- [x] standby scheduler не создаёт runs/jobs;
- [x] Safety Engine проверяет owner/site/epoch/expiry до сети;
- [x] `prepared` сохраняется до network call;
- [x] `network_started` переживает аварийное завершение основной транзакции;
- [x] падение до сети допускает управляемый retry;
- [x] падение после начала сети создаёт manual review без auto-retry;
- [x] Slow Mode не переиспользует attempt number;
- [x] ручная сверка инвалидирует старый epoch;
- [x] failover требует fresh target heartbeat;
- [x] failover блокируется processing/uncertain deliveries;
- [x] независимый Owner/Admin подтверждает переключение;
- [x] Viewer не может создавать/подтверждать failover;
- [x] Prometheus metrics и alerts присутствуют;
- [x] migration round-trip 2.3↔2.2 проверяется release-QA;
- [ ] live failover между двумя хостами подтверждён реальной PostgreSQL и Telegram canary.

## Continuity Assurance 2.4

- [x] simulation не изменяет execution lease;
- [x] simulation не создаёт failover request и Telegram gateway;
- [x] live-drill использует production controlled failover;
- [x] failback возвращает active lease на source с новым epoch;
- [x] несовместимый runtime блокируется до lease mutation;
- [x] policy change инвалидирует незавершённое учение;
- [x] event hash-chain выявляет tampering и cross-tenant перенос;
- [x] evidence проверяется семантически, а не только по digest;
- [x] four-eyes запрещает self-signoff;
- [x] RTO breach нельзя принять;
- [x] expired evidence не удовлетворяет compliance;
- [x] worker projection не создаёт сетевую работу;
- [x] ошибка одного drill изолирована savepoint;
- [x] commissioning и metrics учитывают continuity;
- [x] production config требует continuity gates;
- [x] все именованные функции проходят documentation gate;
- [ ] live двуххостовый PostgreSQL failover/failback выполнен владельцем;
- [ ] canary до и после переключения подтверждена в служебном Telegram-чате.

## Capacity & Backpressure acceptance 2.5

Релиз принимается по capacity-контуру, когда подтверждено:

- policy имеет ordered limits и DB constraints;
- contradictory API и direct-DB writes отклоняются;
- oversized run не создаёт CampaignRun/DeliveryJob;
- scheduler повторно проверяет policy после run-now;
- ready queue блокирует staged release до `held → pending`;
- manual retry не изменяет job при отказе;
- точный minute/hour limit блокирует gateway;
- concurrent `prepared` reservations не занимают один slot дважды;
- final race оставляет attempt `abandoned` без `network_started_at`;
- assessment устаревает после policy change;
- worker evaluation не дублируется внутри интервала;
- tenant isolation и Viewer read-only соблюдаются;
- production не запускается с отключённым gate;
- Prometheus alerts и Grafana panels проходят release validator;
- migration round-trip 2.5↔2.4 проходит;
- полный QA проходит и после распаковки пользовательского ZIP.

Внешняя acceptance отдельно требует load-test целевой PostgreSQL/Redis/worker-среды и контролируемую Telegram-canary.
