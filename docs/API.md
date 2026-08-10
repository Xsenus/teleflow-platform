# TeleFlow Platform API 2.1

Base path: `/api/v1`
OpenAPI in development: `/api/docs`, `/api/redoc`, `/api/openapi.json`

## 1. Authentication

### Browser session

- access JWT in HttpOnly cookie;
- rotating refresh token in HttpOnly cookie;
- CSRF cookie readable by SPA;
- non-GET request sends `X-CSRF-Token` equal to `teleflow_csrf`.

```text
POST   /auth/login
POST   /auth/refresh
POST   /auth/logout
GET    /auth/me
GET    /auth/sessions
DELETE /auth/sessions/{session_id}
POST   /auth/sessions/revoke-others
POST   /auth/change-password
POST   /auth/totp/start
POST   /auth/totp/confirm
POST   /auth/totp/disable
```

`/auth/sessions` возвращает metadata текущего пользователя: created/last-used time, expiry, IP/user-agent preview и признак current. Raw refresh token не возвращается.

### Service API key

External endpoints принимают:

```http
Authorization: Bearer tfp_...
```

или:

```http
X-API-Key: tfp_...
```

Secret показывается один раз. В БД хранится SHA-256 hash и prefix.

Scopes:

- `candidates:read`;
- `candidates:contacts`;
- `conversations:read`;
- `events:write`;
- `*` — только для полностью доверенного сервиса.

## 2. Common behavior

- IDs — opaque strings;
- timestamps — ISO 8601 UTC;
- tenant определяется текущим user/API key;
- чужой tenant object возвращает 404;
- validation error — 422;
- conflict/state error — 409;
- unauthenticated — 401;
- insufficient role/scope — 403;
- request ID возвращается в response header.

Sensitive fields отсутствуют в response schemas: token, api_hash, phone, session, auth code/password, password/TOTP/API-key hashes и encrypted values.

## 3. Health, dashboard and analytics

```text
GET /health/live
GET /health/ready
GET /dashboard/summary
GET /analytics/overview
GET /analytics/timeseries
GET /analytics/export.csv
GET /metrics                    internal only
```

Analytics query parameters:

```text
from=<ISO datetime>
to=<ISO datetime>
```

Overview содержит delivery, conversations, consent, candidate funnel, AI, outbox, campaigns и A/B template delivery. CSV не содержит message bodies или contacts.

## 4. Organization and users

```text
GET   /organization
PATCH /organization
POST  /organization/publishing/pause
POST  /organization/publishing/resume
GET   /users
POST  /users
PATCH /users/{user_id}
DELETE /users/{user_id}
```

Organization содержит timezone, retention policy и governance settings:

```json
{
  "publishing_paused": false,
  "publishing_pause_reason": null,
  "require_distinct_campaign_approver": true,
  "high_risk_destination_threshold": 20,
  "high_risk_required_approvals": 2,
  "approval_request_ttl_hours": 24
}
```

Pause требует `reason` длиной не менее пяти символов, удерживает ожидающие jobs и доступен owner/admin. Resume принимает необязательный `note`. Нельзя удалить или отключить последнего owner.

## 5. Telegram connections

```text
GET  /connections
GET  /connections/{id}
POST /connections/bot
POST /connections/user/start
POST /connections/user/complete
PATCH /connections/{id}
DELETE /connections/{id}
POST /connections/{id}/health
GET  /connections/{id}/discover
POST /connections/{id}/pause
POST /connections/{id}/resume
```

`resume` после safety event требует explicit acknowledgement. `discover` только перечисляет dialogs, к которым connection уже имеет доступ; он не вступает в группы.

## 6. Destinations

```text
GET/POST         /destinations
GET/PATCH/DELETE /destinations/{id}
POST             /destinations/{id}/validate
POST             /destinations/bulk/preview
POST             /destinations/bulk/apply
POST             /destinations/bulk/discovery
GET              /destinations/export.csv
```

Destination поддерживает:

- `timezone_name`;
- `allowed_weekdays`;
- `allowed_start_time`;
- `allowed_end_time`;
- `cooldown_minutes_override`;
- `permission_reviewed_at`;
- `permission_expires_at`;
- permission note/rules URL;
- topic ID.

`permission_expires_at` задаётся только для подтверждённого разрешения и должен быть в будущем. При переводе назначения в `unverified`/`denied` срок и отметка проверки очищаются. Истёкшее разрешение блокирует preflight, scheduler и delivery safety до сетевого обращения к Telegram.

Bulk endpoints используют `multipart/form-data` для TXT/CSV/TSV. Preview не меняет БД. Discovery принимает выбранные items из уже полученного списка dialogs и повторно разрешает каждый chat через gateway.

## 7. Media and templates

```text
GET/POST         /media
DELETE           /media/{id}
GET/POST         /templates
GET/PATCH/DELETE /templates/{id}
```

Media upload использует `multipart/form-data`. До storage выполняются size/MIME/signature checks и при включении ClamAV scan.

## 8. Campaigns, approvals and jobs

```text
GET/POST        /campaigns
GET/PATCH       /campaigns/{id}
PUT             /campaigns/{id}/destinations
GET             /campaigns/{id}/preview
POST            /campaigns/{id}/preflight
GET             /campaigns/{id}/preflight/latest
GET             /campaigns/{id}/approval
POST            /campaigns/{id}/submit-approval
GET             /campaigns/approval-requests
POST            /campaigns/approval-requests/{request_id}/decision
POST            /campaigns/{id}/approve        compatibility endpoint
POST            /campaigns/{id}/run-now
POST            /campaigns/{id}/pause
POST            /campaigns/{id}/resume
POST            /campaigns/{id}/cancel
GET             /campaigns/{id}/runs
POST            /campaigns/{id}/runs/{run_id}/continue
POST            /campaigns/{id}/runs/{run_id}/abort

GET             /jobs
GET             /jobs/{id}
POST            /jobs/{id}/retry
POST            /jobs/{id}/cancel
POST            /jobs/{id}/resolve
```

Campaign fields:

```json
{
  "template_id": "template-a",
  "secondary_template_id": "template-b-or-null",
  "secondary_template_weight": 35,
  "destination_ids": ["destination-1", "destination-2"],
  "rollout_mode": "staged",
  "rollout_batch_size": 5,
  "rollout_pause_seconds": 300,
  "rollout_require_checkpoint": true,
  "rollout_failure_threshold_percent": 20,
  "duplicate_guard_minutes": 1380,
  "approved_fingerprint": null,
  "active_approval_request_id": null
}
```

При наличии secondary template его weight должен быть 1–99. Assignment стабилен для пары campaign/destination.

Preview item содержит точный будущий template/body и назначенное время:

```json
{
  "destination_id": "...",
  "destination_title": "Разрешённая группа",
  "due_at": "2026-08-06T18:00:00Z",
  "template_id": "...",
  "template_name": "Вакансия B",
  "template_variant": "B",
  "body": "...",
  "permission_status": "confirmed",
  "enabled": true,
  "batch_number": 1,
  "content_fingerprint": "sha256-hex",
  "blockers": [],
  "warnings": []
}
```

### Persisted preflight

`POST /campaigns/{id}/preflight` создаёт новый сохранённый отчёт и никогда не переиспользует старый снимок. `GET /campaigns/{id}/preflight/latest` возвращает последний отчёт для UI/аудита.

Проверяются:

- актуальность approval fingerprint и локальная целостность кампании;
- подтверждение, основание и срок действия разрешения назначения;
- Telegram validation flag;
- индивидуальные окна и cooldown;
- наличие media object;
- недавний идентичный content fingerprint в рамках `duplicate_guard_minutes`;
- распределение назначений по staged batches.

Статусы отчёта: `passed`, `warning`, `blocked`, `expired`. Даже успешный отчёт имеет TTL. Scheduler всегда создаёт **свежий** preflight непосредственно перед run, потому что разрешение, cooldown, media и история дублей могли измениться после утверждения.

### Staged rollout

При `rollout_mode=staged` jobs первого пакета создаются как `pending`, последующих — как `held`. Worker не выбирает `held`. После завершения активного пакета run получает `awaiting_checkpoint`, если включена ручная проверка, либо следующий пакет раскрывается автоматически только при доле ошибок не выше установленного порога и отсутствии `waiting_review`.

Checkpoint:

```json
{
  "note": "Пакет проверен в Telegram, удалений и неожиданных дублей нет"
}
```

Abort:

```json
{
  "reason": "Администратор группы запросил остановку публикаций"
}
```

Abort невозможен при job в `processing`; ожидающие/удержанные jobs отменяются, кампания ставится на паузу, а утверждение аннулируется.

### Ручная сверка неоднозначной доставки

`POST /jobs/{id}/resolve` применим только к `waiting_review` с `DELIVERY_RESULT_UNCERTAIN`. Решения:

```json
{
  "resolution": "confirmed_sent",
  "telegram_message_id": "12345",
  "note": "Сообщение найдено в целевой теме вручную"
}
```

- `confirmed_sent` — фиксирует фактическую отправку и cooldown;
- `confirmed_not_sent` — создаёт один явный retry только после повторной проверки connection, destination, campaign и approval;
- `skipped` — закрывает результат без отправки.

Обычные retry/cancel, campaign cancel, staged abort и возобновление connection не обходят этот процесс. Отзыв connection разрешён как security-действие, но uncertain job сохраняется для `confirmed_sent`/`skipped`; credentials уничтожаются, поэтому retry после `confirmed_not_sent` невозможен до нового активного подключения.

### Approval workflow

1. Клиент получает preview и устраняет blockers.
2. `POST /{id}/submit-approval` создаёт immutable request fingerprint.
3. Owner/admin отправляет `approve` или `reject` в decision endpoint.
4. При high-risk policy запрос остаётся `pending`, пока не набрано требуемое число уникальных решений.
5. `run-now`/`resume` разрешены только при текущем fingerprint.

Пример decision:

```json
{
  "decision": "approve",
  "note": "Правила групп и финальный текст проверены"
}
```

Approval response содержит `required_approvals`, `require_distinct_requester`, `expires_at`, status и decisions. Изменение route/schedule/template/destination permission отменяет active request и очищает approval. Просроченные pending requests закрывает scheduler с audit/notification.

`POST /{id}/approve` сохранён для совместимости: в простой policy он создаёт запрос и одно решение в одной транзакции; при four-eyes создаёт только pending request. Новый UI использует явные endpoints.

Run/job creation идемпотентно. При organization emergency stop, stale approval или отсутствии актуального решения API возвращает `409`, а worker не вызывает Telegram gateway.

## 9. Notifications

```text
GET  /notifications
GET  /notifications/counts
POST /notifications/{notification_id}/read
POST /notifications/read-all
POST /notifications/{notification_id}/acknowledge
```

Фильтры list endpoint включают status/severity/event type и pagination. Critical notification остаётся `unacknowledged`, пока owner/admin явно не подтвердит его. Read и acknowledge являются разными действиями. Повторяющиеся события могут объединяться через `occurrence_count` и `last_occurred_at`.

## 10. Telegram Business

Management:

```text
POST   /business/bots/{connection_id}/webhook
GET    /business/bots/{connection_id}/webhook
DELETE /business/bots/{connection_id}/webhook
GET    /business/connections
PATCH  /business/connections/{business_id}
```

Public Telegram callback:

```text
POST /hooks/telegram/{connection_id}/{path_token}
```

Header:

```http
X-Telegram-Bot-Api-Secret-Token: <secret>
```

Webhook отвечает после durable insert, до AI/Business reply.

## 11. Conversations and candidates

```text
GET    /conversations
GET    /conversations/{id}
PATCH  /conversations/{id}
POST   /conversations/{id}/close
GET    /conversations/{id}/messages
GET    /conversations/{id}/messages/{message_id}/body
POST   /conversations/{id}/reply

GET    /candidates
GET    /candidates/{id}
PATCH  /candidates/{id}
GET    /candidates/{id}/contact
GET    /candidates-export.csv
```

List DTO содержит preview, но не full body/phone/email. Раскрытие PII отделено для audit и RBAC.

## 12. Automation flows, policies, providers and knowledge

```text
GET/POST          /automation/flows
GET/PATCH/DELETE  /automation/flows/{flow_id}

GET/POST          /automation/policies
PATCH/DELETE      /automation/policies/{id}

GET/POST          /automation/providers
PATCH/DELETE      /automation/providers/{id}
POST              /automation/providers/{id}/test

GET/POST          /automation/knowledge
PATCH/DELETE      /automation/knowledge/{id}
```

Flow definition:

```json
{
  "version": 1,
  "start_node_id": "welcome",
  "nodes": [
    {"id": "welcome", "type": "message", "text": "Здравствуйте", "next_node_id": "name"},
    {"id": "name", "type": "question", "text": "Как вас зовут?", "field": "full_name", "validation": "text", "next_node_id": "done"},
    {"id": "done", "type": "end", "text": "Спасибо", "completion_mode": "handoff"}
  ]
}
```

Graph проходит validation. Active revision read-only; изменение требует новой revision. External AI URL проходит SSRF validation.

## 13. Integrations and outbox

```text
GET/POST          /integrations
PATCH/DELETE      /integrations/{id}
POST              /integrations/{id}/test
GET               /integrations/outbox/events
POST              /integrations/outbox/{event_id}/retry
```

Webhook request:

```http
Content-Type: application/json
X-TeleFlow-Event-ID: <event id>
X-TeleFlow-Event-Type: candidate.updated
X-TeleFlow-Signature: sha256=<hex hmac>
```

Подписывается raw body secret выбранного endpoint. Получатель должен быть идемпотентным по event ID.

## 14. Privacy

```text
GET/POST /privacy/requests
POST     /privacy/requests/{id}/process
GET      /privacy/requests/{id}/download
POST     /privacy/retention/run
```

Request type: `export` или `delete`. Subject задаётся одним из `conversation_id`, `telegram_user_id`, `telegram_chat_id`.

## 15. API keys and external API

Management:

```text
GET/POST /api-keys
POST     /api-keys/{id}/revoke
```

Service endpoints:

```text
GET  /external/candidates                 scope candidates:read
GET  /external/candidates/{id}/contact    scope candidates:contacts
GET  /external/conversations              scope conversations:read
POST /external/events                     scope events:write
```

## 16. Audit

```text
GET /audit
GET /audit/verify
```

List rows содержат `sequence`, `prev_hash`, `entry_hash` и `chain_version`. Verify пересчитывает tenant hash-chain и возвращает `valid`, число checked/legacy entries, first error, computed/stored head и время проверки. Details проходят redaction. System chain проверяется offline CLI с правами оператора хоста, а не browser tenant API.

## 17. Webhook signature verification example

```python
import hashlib
import hmac

expected = (
    "sha256="
    + hmac.new(
        endpoint_secret.encode(),
        raw_request_body,
        hashlib.sha256,
    ).hexdigest()
)
assert hmac.compare_digest(expected, signature_header)
```

## 18. Production Pilot 1.4

Все endpoints используют browser auth, tenant scope и стандартную CSRF-защиту для изменяющих операций.

### Destination validation

```text
POST /api/v1/destinations/validate-batch
GET  /api/v1/destinations/{destination_id}/validation-history
POST /api/v1/destinations/{destination_id}/validate
```

Batch request:

```json
{
  "destination_ids": ["uuid-1", "uuid-2"]
}
```

Batch response содержит агрегаты `passed`, `failed`, `write_forbidden`, `deferred` и построчные результаты. Максимум — 50 уникальных IDs. После Telegram error с `retry_after` остаток пакета не проверяется.

History response не содержит Telegram credentials. Записи включают status, capabilities, error, source, checked_by и checked_at.

### Publishing blackouts

```text
GET    /api/v1/blackouts
POST   /api/v1/blackouts
PATCH  /api/v1/blackouts/{blackout_id}
DELETE /api/v1/blackouts/{blackout_id}
GET    /api/v1/blackouts/evaluate/current
```

`scope`:

```text
organization | connection | destination
```

`kind`:

```text
one_time | weekly
```

One-time использует `starts_at`/`ends_at`. Weekly использует `timezone_name`, `weekdays`, `start_time`, `end_time`. Окна могут пересекать полночь.

Evaluation query принимает необязательные `connection_id`, `destination_id`, `at` и возвращает `active`, общий `defer_until` и список совпавших правил.

### Pilot readiness

```text
GET  /api/v1/pilot/overview
GET  /api/v1/pilot/readiness
POST /api/v1/pilot/readiness/{campaign_id}
GET  /api/v1/pilot/readiness/{campaign_id}/latest
```

Report response:

```json
{
  "status": "warning",
  "fingerprint": "sha256...",
  "checks": [
    {
      "code": "DESTINATION_VALIDATION",
      "title": "Проверка назначений",
      "status": "passed",
      "message": "Все назначения имеют свежую проверку Telegram",
      "details": {}
    }
  ],
  "blockers": [],
  "warnings": ["Telegram transport: Включён fake mode"],
  "expires_at": "2026-08-06T12:30:00Z"
}
```

При `TELEFLOW_PILOT_READINESS_REQUIRED=true` endpoints запуска/возобновления возвращают `409`, если report отсутствует, blocked, expired или имеет другой campaign fingerprint.

## 19. Pilot Certification & Diagnostics 1.5

Read-only stage/canary endpoints доступны всем аутентифицированным ролям. Изменение этапа, canary и support bundle требуют Owner/Admin.

### Stage overview and assessments

```text
GET  /api/v1/pilot/stage
GET  /api/v1/pilot/stage/assessments
POST /api/v1/pilot/stage/assess
POST /api/v1/pilot/stage/advance
POST /api/v1/pilot/stage/lower
```

Assessment request:

```json
{
  "requested_stage": "five"
}
```

Advance request:

```json
{
  "assessment_id": "uuid",
  "confirmation": "ПЕРЕЙТИ НА ЭТАП 5",
  "note": "Реальный запуск одной служебной группы проверен"
}
```

Lower request:

```json
{
  "target_stage": "service",
  "confirmation": "СНИЗИТЬ ЭТАП ДО 1",
  "reason": "Возврат к служебному пилоту после ручной проверки"
}
```

Ответ stage overview содержит текущий/следующий этап, числовые лимиты, флаг enforcement, последнюю оценку и время свежей успешной live-canary.

### One-shot canary

```text
GET  /api/v1/pilot/canaries
POST /api/v1/pilot/canaries
```

```json
{
  "destination_id": "uuid",
  "confirmation": "ОТПРАВИТЬ СЛУЖЕБНОЕ СООБЩЕНИЕ"
}
```

Body сообщения серверный и неизменяемый. Endpoint возвращает `sent`, `blocked` или `failed`, marker, SHA-256 body, fake-флаг, Telegram message ID и безопасный error code. Blocked pre-check также возвращается с HTTP 201, потому что попытка сохраняется как завершённый аудируемый объект; повторного Telegram-запроса нет.

### Redacted support bundle

```text
GET    /api/v1/pilot/support-bundles
POST   /api/v1/pilot/support-bundles
GET    /api/v1/pilot/support-bundles/{bundle_id}/download
DELETE /api/v1/pilot/support-bundles/{bundle_id}
```

Create request содержит только причину. Download возвращает ZIP с `Cache-Control: no-store`; сервер перед выдачей проверяет SHA-256. Истёкший или удалённый объект возвращает `410`.
## 20. Commissioning & Portability 1.6

### Commissioning reports

```text
GET  /api/v1/commissioning
GET  /api/v1/commissioning/latest
POST /api/v1/commissioning/run
```

`POST` доступен Owner/Admin. Report хранит `status`, `checks`, `blockers`, `warnings`, `fingerprint`, `summary`, `created_at` и `expires_at`. Ответ не содержит секретов.

### Formal pilot programs

```text
GET  /api/v1/pilot/programs
POST /api/v1/pilot/programs
GET  /api/v1/pilot/programs/{program_id}
POST /api/v1/pilot/programs/{program_id}/stages/{stage_id}/refresh
POST /api/v1/pilot/programs/{program_id}/stages/{stage_id}/start
POST /api/v1/pilot/programs/{program_id}/stages/{stage_id}/attach-run
POST /api/v1/pilot/programs/{program_id}/stages/{stage_id}/signoff
POST /api/v1/pilot/programs/{program_id}/cancel
GET  /api/v1/pilot/programs/{program_id}/acceptance-report
```

`stage_sizes` принимает только возрастающее подмножество `1,5,20,50,100`, начинающееся с `1`. Evidence конкретного run становится неизменяемым после attach и проверяется по SHA-256 перед sign-off.

### Configuration bundles

```text
GET    /api/v1/configuration-bundles
POST   /api/v1/configuration-bundles/export
POST   /api/v1/configuration-bundles/preview
POST   /api/v1/configuration-bundles/import?conflict_mode=skip|rename
GET    /api/v1/configuration-bundles/{bundle_id}/download
DELETE /api/v1/configuration-bundles/{bundle_id}
```

Export/import/download/delete доступны Owner/Admin. Import всегда создаёт fail-safe состояние без credentials, active permission, approval или автоматического запуска. Download содержит `Cache-Control: no-store` и `X-Content-SHA256`.

## 21. Artifact Trust & Signed Evidence 1.7

### Signing keys

```text
GET  /api/v1/artifact-signing-keys
POST /api/v1/artifact-signing-keys/generate
POST /api/v1/artifact-signing-keys/import
PATCH /api/v1/artifact-signing-keys/{key_id}
POST /api/v1/artifact-signing-keys/{key_id}/default
POST /api/v1/artifact-signing-keys/{key_id}/revoke
GET  /api/v1/artifact-signing-keys/{key_id}/public
```

Generate request:

```json
{
  "name": "Основной production signer",
  "make_default": true,
  "trusted_for_import": true,
  "note": "Ключ создан при вводе инсталляции в эксплуатацию"
}
```

Import request принимает только PEM или base64 публичной части:

```json
{
  "name": "Доверенный ключ исходного сервера",
  "public_key": "-----BEGIN PUBLIC KEY-----...",
  "trusted_for_import": true
}
```

Responses содержат public metadata и вычисляемое `has_private_key`, но никогда не содержат `private_key_enc` или plaintext private key.

Revoke request:

```json
{
  "reason": "Ключ заменён после плановой ротации"
}
```

Отзыв необратимо удаляет локальную приватную часть. Public key/fingerprint сохраняются для исторической проверки.

### Artifact inspection

```text
POST /api/v1/artifact-signing-keys/verify-artifact
Content-Type: multipart/form-data
file=<configuration ZIP | support ZIP | acceptance JSON>
```

Ответ:

```json
{
  "artifact_type": "configuration_bundle",
  "integrity_valid": true,
  "sha256": "...",
  "size_bytes": 12345,
  "signature": {
    "status": "valid_trusted",
    "signed": true,
    "cryptographically_valid": true,
    "trusted": true,
    "fingerprint": "...",
    "key_id": "ed25519-...",
    "purpose": "teleflow.configuration_bundle.manifest.v1",
    "signer_known": true,
    "signer_revoked": false,
    "error": null
  },
  "details": {}
}
```

API verification учитывает tenant-local trust store. Подпись известным отозванным ключом получает `revoked`, даже если Ed25519 math остаётся корректной.

### Signed downloads

Configuration/support downloads добавляют:

```text
X-Artifact-Signature-Status
X-Artifact-Signer-Fingerprint
Cache-Control: no-store
```

Pilot acceptance report добавляет `signature` в JSON wrapper и тот же status header.

### Signature policy

```text
TELEFLOW_ARTIFACT_SIGNATURE_POLICY=optional|required_valid|require_trusted
```

- `optional`: unsigned legacy разрешён, invalid/revoked запрещены;
- `require_valid`: нужна корректная Ed25519-подпись;
- `require_trusted`: signer должен быть активным и явно доверенным организации.

Production runtime принимает только `require_trusted`.

## 22. Recovery Assurance API 1.8

### Получение статуса

```http
GET /api/v1/recovery/status
```

Возвращает policy, checks, blockers, warnings, последний verified backup и последний restore drill.

### Политика

```http
GET   /api/v1/recovery/policy
PATCH /api/v1/recovery/policy
```

Изменение доступно Owner/Admin и защищено CSRF. В production нельзя отключить recovery, encryption, trusted receipt и restore drill.

### Backup evidence

```http
GET  /api/v1/recovery/backups
POST /api/v1/recovery/backups/import
```

Import принимает только signed JSON receipt ограниченного размера. Raw archive не загружается. Новый evidence имеет `registered`, пока CLI не проверил физический archive.

### Restore drill evidence

```http
GET  /api/v1/recovery/drills
POST /api/v1/recovery/drills/import
```

Import проверяет signature, organization, backup binding, timestamps и RTO evidence. Успешный signed receipt не исправляет неуспешный status.

CLI создания/проверки описан в [RECOVERY_ASSURANCE.md](RECOVERY_ASSURANCE.md).

## 23. Change Management API 1.9

```text
GET  /api/v1/changes
POST /api/v1/changes
POST /api/v1/changes/{id}/approve
GET  /api/v1/changes/{id}/verifications
POST /api/v1/changes/{id}/verify/{phase}
POST /api/v1/changes/{id}/start
POST /api/v1/changes/{id}/complete
POST /api/v1/changes/{id}/fail
POST /api/v1/changes/{id}/cancel
POST /api/v1/changes/maintenance/start
POST /api/v1/changes/maintenance/stop
```

Для upgrade требуется `target_version`. Автор change request не может самостоятельно его одобрить. Для upgrade/database migration/infrastructure старт требует maintenance mode и успешный свежий pre-change verification. Завершение выполняет post-change verification и блокируется при migration/version drift. Все write-маршруты используют CSRF, Owner/Admin RBAC, tenant isolation и audit-chain.

## Release Trust 2.0

```text
GET  /api/v1/release-attestations
POST /api/v1/release-attestations/local
POST /api/v1/release-attestations/import
POST /api/v1/release-attestations/{id}/verify
```

`POST /local` подписывает provenance текущей установленной сборки основным Ed25519-ключом организации. `POST /import` принимает canonical payload и signature envelope. Для production upgrade `ChangeRequest.release_attestation_id` должен указывать на `valid_trusted` attestation той же целевой версии.

## Supply Chain Assurance 2.1

### Policy

```text
GET   /api/v1/supply-chain/policy
PATCH /api/v1/supply-chain/policy
```

PATCH доступен Owner/Admin. `report_ttl_hours` не может превышать серверный `TELEFLOW_DEPENDENCY_ASSESSMENT_TTL_HOURS`.

### Assessments and SBOM

```text
GET  /api/v1/supply-chain/assessments
POST /api/v1/supply-chain/assessments/{attestation_id}
POST /api/v1/supply-chain/assessments/{attestation_id}/inventory
POST /api/v1/supply-chain/assessments/{assessment_id}/verify
GET  /api/v1/supply-chain/assessments/{assessment_id}/sbom
GET  /api/v1/supply-chain/attestations/{attestation_id}/sbom
```

Импортируемый report должен содержать точные `release_payload_sha256` и `sbom_sha256`. Download endpoints возвращают `Cache-Control: no-store`.

### Transparency

```text
GET  /api/v1/supply-chain/transparency
GET  /api/v1/supply-chain/transparency/verify
POST /api/v1/supply-chain/transparency/{attestation_id}/publish
POST /api/v1/supply-chain/transparency/{attestation_id}/withdraw
```

Publish/withdraw доступны Owner/Admin; withdraw требует осмысленную причину. Verification endpoint является read-only и возвращает целостность цепочки, sequence/hash последней записи и число активных releases.

### Change binding

`POST /api/v1/changes` принимает `release_dependency_assessment_id`. Для upgrade он должен относиться к тому же `release_attestation_id`; backend отклоняет cross-release и cross-tenant IDs.

## Operational SLO & Incident Assurance 2.2

### SLO overview and policy

```http
GET /api/v1/operations/overview
GET /api/v1/operations/slo-policy
PATCH /api/v1/operations/slo-policy
GET /api/v1/operations/slo-assessments?limit=100
POST /api/v1/operations/slo-assessments
```

`PATCH /slo-policy` доступен Owner/Admin. Ручную оценку могут запускать Owner/Admin/Operator. Viewer имеет read-only доступ.

Assessment возвращает `policy_snapshot`, `policy_sha256`, `metrics`, `checks`, `blockers`, `warnings`, `fingerprint` и `expires_at`.

### Инциденты

```http
GET   /api/v1/operations/incidents
POST  /api/v1/operations/incidents
GET   /api/v1/operations/incidents/{incident_id}
PATCH /api/v1/operations/incidents/{incident_id}
GET   /api/v1/operations/incidents/{incident_id}/events
POST  /api/v1/operations/incidents/{incident_id}/acknowledge
POST  /api/v1/operations/incidents/{incident_id}/mitigate
POST  /api/v1/operations/incidents/{incident_id}/resolve
POST  /api/v1/operations/incidents/{incident_id}/comment
POST  /api/v1/operations/incidents/{incident_id}/close
POST  /api/v1/operations/incidents/{incident_id}/reopen
```

Каждое action body содержит обязательный `note`. Для `resolve` дополнительно можно передать `root_cause` и `postmortem_url`. Закрытие и повторное открытие доступны только Owner/Admin.

`POST /operations/incidents` всегда создаёт операторский источник `manual`. Поле `source` из клиентского JSON игнорируется схемой; системные источники `slo`, `delivery`, `worker`, `change` и `security` создаются только доверенными backend-контурами.

## Execution Fencing & Failover API 2.3

Все маршруты tenant-scoped. Чтение доступно аутентифицированным пользователям организации; изменения — Owner/Admin.

### `GET /api/v1/execution/overview`

Возвращает текущую площадку приложения, primary site, execution lease, зарегистрированные sites, открытый failover и счётчики processing/uncertain/network-started.

### `GET /api/v1/execution/sites`

Список площадок с вычисленными `online` и `is_active_site`.

### `GET /api/v1/execution/failovers`

История failover requests.

### `POST /api/v1/execution/failovers`

```json
{
  "target_site_key": "standby-eu-2",
  "reason": "Плановое переключение перед обслуживанием primary"
}
```

Переводит source lease в draining. Target обязан иметь свежий heartbeat.

### `POST /api/v1/execution/failovers/{id}/approve`

```json
{
  "confirmation": "ПЕРЕКЛЮЧИТЬ НА standby-eu-2"
}
```

При включённом four-eyes подтверждение выполняет другой Owner/Admin. Processing и unresolved attempts возвращают `409` и сохраняются в `blockers`.

### `POST /api/v1/execution/failovers/{id}/cancel`

Отменяет открытый request и возвращает исходную площадку в active.

### `GET /api/v1/execution/delivery-attempts`

Параметры:

```text
job_id — необязательный фильтр
limit  — 1..500
```

Возвращает durable attempt ledger без Telegram credentials и содержимого секретов.

## Continuity Assurance 2.4

```text
GET   /api/v1/continuity/overview
GET   /api/v1/continuity/policy
PATCH /api/v1/continuity/policy
GET   /api/v1/continuity/drills
POST  /api/v1/continuity/drills
GET   /api/v1/continuity/drills/{drill_id}
POST  /api/v1/continuity/drills/{drill_id}/start
POST  /api/v1/continuity/drills/{drill_id}/failback
POST  /api/v1/continuity/drills/{drill_id}/signoff
POST  /api/v1/continuity/drills/{drill_id}/cancel
GET   /api/v1/continuity/drills/{drill_id}/events
GET   /api/v1/continuity/drills/{drill_id}/verify
```

Mutation endpoints требуют Owner/Admin и CSRF. Все запросы фильтруются по `organization_id`; UUID чужой организации возвращает 404. Domain conflicts возвращаются как HTTP 409 без частично сохранённого перехода.

## Capacity API 2.5

### `GET /api/v1/capacity/overview`

Возвращает policy, текущие queue/rate metrics, последний assessment, актуальность evidence и итоговые admission/dispatch признаки.

### `GET /api/v1/capacity/policy`

Возвращает tenant policy. При первом чтении создаёт безопасную policy по настройкам инсталляции.

### `PATCH /api/v1/capacity/policy`

Owner/Admin. Частично изменяет policy, но доменный сервис повторно валидирует всю итоговую строку. Изменение policy делает предыдущее evidence неактуальным по SHA-256.

### `GET /api/v1/capacity/assessments`

Возвращает историю immutable assessments текущей организации.

### `POST /api/v1/capacity/assessments`

Owner/Admin. Создаёт ручной assessment по текущему состоянию очереди.

### Ошибки

```text
CAPACITY_ADMISSION_BLOCKED
CAPACITY_READY_QUEUE_BLOCKED
CAPACITY_PROCESSING_LIMIT
CAPACITY_MINUTE_RATE_LIMIT
CAPACITY_HOUR_RATE_LIMIT
CAPACITY_RESERVATION_MISSING
```

Capacity rejection возвращается до Telegram network call. Клиент не должен автоматически обходить отказ через другой endpoint.
