# TeleFlow Platform 1.2.0 — Governance & Resilience

Дата релиза: **6 августа 2026 года**

Версия 1.2.0 усиливает TeleFlow Platform не за счёт увеличения объёма автоматической рассылки, а за счёт контролируемого допуска публикаций, централизованной аварийной остановки, операторских уведомлений и проверяемого аудита. Все прежние ограничения сохраняются: нет auto-join, массовых личных сообщений, ротации аккаунтов/прокси и обхода ограничений Telegram.

## 1. Явное утверждение кампаний

Запуск кампании больше не создаёт разрешение неявно. Полный lifecycle:

```text
черновик
→ preview
→ запрос утверждения
→ одно или несколько решений
→ актуальный fingerprint
→ scheduled/run-now
→ Safety Engine перед каждым сетевым вызовом
```

Добавлены сущности `CampaignApprovalRequest` и `CampaignApprovalDecision`. Запрос содержит:

- снимок fingerprint разрешаемой конфигурации;
- автора запроса;
- требуемое число решений;
- признак независимого утверждающего;
- срок действия;
- статус `pending/approved/rejected/cancelled/expired`;
- историю решений и комментарии.

`run-now` и `resume` возвращают конфликт, если утверждение отсутствует, просрочено или fingerprint больше не совпадает.

## 2. Правило «четырёх глаз» и высокорисковые маршруты

На уровне организации настраиваются:

- `require_distinct_campaign_approver` — автор запроса не может одобрить его сам;
- `high_risk_destination_threshold` — порог количества активных назначений;
- `high_risk_required_approvals` — число независимых решений выше порога;
- `approval_request_ttl_hours` — срок действия запроса.

Backend заранее проверяет, что в организации достаточно активных владельцев/администраторов для выбранной политики. Уникальность решения каждого пользователя защищена ограничением БД и сериализацией изменения строк.

## 3. Fingerprint утверждённой конфигурации

SHA-256 fingerprint включает данные, которые реально влияют на публикацию:

- подключение и safety-параметры;
- шаблон A/B, ревизию, hash текста и media asset;
- расписание, timezone, интервалы и окончание кампании;
- список и порядок назначений;
- chat/topic identifiers;
- permission status, evidence и время подтверждения;
- индивидуальные дни, окна и cooldown;
- custom body каждой связи кампании с назначением.

Редактирование кампании, замена маршрута, изменение шаблона, расписания или правил назначения аннулирует активное разрешение. Worker повторно вычисляет fingerprint непосредственно перед отправкой.

## 4. Глобальный аварийный стоп

Владелец или администратор может остановить публикации всей организации. Операция:

- фиксирует причину, автора и время;
- переводит ожидающие `pending/retry` jobs в `waiting_review` с кодом `ORG_EMERGENCY_STOP`;
- блокирует scheduler;
- блокирует Safety Engine до Telegram network call;
- создаёт критический audit event, notification и outbox event;
- отображает красную глобальную полосу во всей панели.

Возобновление является отдельным явным действием. Удержанные jobs возвращаются в очередь, но всё равно повторно проходят актуальность утверждения, права назначения, интервалы и остальные safety-проверки.

## 5. Центр уведомлений

Добавлен tenant-scoped центр операторских уведомлений:

- статусы `unread/read/acknowledged`;
- уровни `info/warning/critical`;
- счётчики в topbar и dashboard;
- массовая отметка прочитанными;
- обязательное acknowledgement критических событий;
- ссылки на campaign/connection/destination/job;
- временная дедупликация одинаковых событий;
- outbox event `notification.created` для внешних интеграций.

Уведомления создаются для FloodWait, anti-spam, потери авторизации, запрета записи, неоднозначной доставки, safety-блокировок, запросов/решений/истечения утверждений и аварийной остановки.

## 6. Tamper-evident audit hash-chain

Каждая новая audit-запись получает:

- монотонный `sequence` внутри организации;
- `prev_hash` предыдущей записи;
- `entry_hash` SHA-256 канонического payload;
- `chain_version`.

Отдельный `AuditChainState` хранит head цепочки. Проверка обнаруживает:

- изменение защищённых полей записи;
- нарушение последовательности;
- замену `prev_hash`;
- удаление/подмену chain state;
- несовпадение последнего hash/sequence.

CLI:

```bash
python scripts/audit_chain.py verify --include-system --json
python scripts/audit_chain.py backfill --include-system --yes
```

Исторические записи 1.1 не модифицируются online migration. Их нужно связать offline после остановки API/worker, чтобы backfill не конкурировал с новыми событиями. Hash-chain повышает обнаруживаемость локальной подмены, но не заменяет внешний immutable/WORM-export и независимый SIEM.

## 7. Ротация master key

Добавлены сервис и CLI для контролируемой ротации AES-GCM master key:

```bash
TELEFLOW_NEW_MASTER_KEY='<new-key>' \
python scripts/rotate_master_key.py --dry-run

TELEFLOW_NEW_MASTER_KEY='<new-key>' \
python scripts/rotate_master_key.py --yes
```

Preflight расшифровывает каждое поддерживаемое encrypted field старым ключом до изменения данных. Покрыты:

- TOTP secrets;
- Bot API token, MTProto `api_hash` и StringSession;
- MTProto auth challenge data;
- Telegram Business raw update;
- conversation message body;
- candidate phone/email;
- AI provider key;
- integration endpoint config;
- завершённые encrypted privacy export objects.

Новый ключ проверяется на формат и минимальную криптографическую длину. Plaintext не выводится в stdout/audit. Для реальной ротации обязательны остановка сервисов, проверенный backup и последующее обновление `TELEFLOW_MASTER_KEY` перед запуском. Транзакция БД и внешнее S3-хранилище не могут быть crash-atomic, поэтому процедура предусматривает preflight и best-effort rollback; это явно отражено в runbook.

## 8. Изменения API и UI

Новые API-группы:

```text
GET/PATCH  /api/v1/organization
POST       /api/v1/organization/publishing/pause
POST       /api/v1/organization/publishing/resume

GET        /api/v1/campaigns/approval-requests
GET        /api/v1/campaigns/{id}/approval
POST       /api/v1/campaigns/{id}/submit-approval
POST       /api/v1/campaigns/approval-requests/{id}/decision

GET        /api/v1/notifications
GET        /api/v1/notifications/counts
POST       /api/v1/notifications/{id}/read
POST       /api/v1/notifications/read-all
POST       /api/v1/notifications/{id}/acknowledge

GET        /api/v1/audit/verify
```

UI получил:

- раздел «Уведомления»;
- badge непрочитанных и критических событий;
- прогресс утверждения в списке/карточке кампании;
- форму решения с approve/reject;
- настройки governance в организации;
- глобальную полосу аварийной остановки;
- проверку hash-chain в разделе аудита.

## 9. Миграция

Alembic head: `f4a6b8c12d34`.

Рекомендуемый порядок 1.1→1.2:

1. Создать и проверить backup БД/storage.
2. Остановить API и worker.
3. Разместить код 1.2 и установить зависимости.
4. Выполнить `alembic upgrade head`.
5. Выполнить offline audit backfill.
6. Выполнить audit verify.
7. Запустить сервисы.
8. Проверить dashboard, уведомления, governance policy и fake campaign.
9. Только после этого подключать live Telegram.

Миграционный roundtrip `1.1 → 1.2 → 1.1 → 1.2` входит в release-QA.

## 10. QA

Проверено автоматизированно:

- 96 тестов;
- statement coverage 74,15% при gate 65%;
- Python compileall;
- JavaScript syntax для SPA и service worker;
- свежая миграция, `alembic check`, downgrade/upgrade roundtrip;
- HTTP login/cookies/CSRF/dashboard/logout smoke;
- global stop до сетевого вызова;
- stale fingerprint и изменение маршрута;
- one- и multi-approval policy;
- four-eyes и недостаточное число approvers;
- proactive approval expiry;
- notification dedup/read/ack;
- audit tamper/missing-state detection и backfill;
- master-key dry-run/rotation/weak-key rejection;
- один изолированный worker cycle.

Точные результаты итогового архива находятся в `docs/QA_REPORT.md` и `BUILD_INFO.json`.

## 11. Внешние gates

Как и прежде, исходный архив не содержит секретов владельца. Отдельной live-приёмки требуют Bot API, MTProto, Telegram Business, внешний AI, Google Sheets, S3, ClamAV, production TLS и восстановление резервной копии на целевом host. Governance 1.2 не отменяет необходимости проверять правила каждой группы и не обещает «безопасного антибан-интервала».
