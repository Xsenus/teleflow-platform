# TeleFlow Platform 1.1.0 — release notes

Дата релиза: **6 августа 2026 года**

## Обзор

Версия 1.1.0 развивает production-релиз 1.0 в направлениях, которые требуются для ежедневной эксплуатации: onboarding групп, индивидуальные правила публикаций, визуальная квалификация кандидатов, аналитика, управление browser sessions, PWA и дополнительная защита uploads.

Релиз не добавляет механизмы обхода Telegram. Auto-join, массовые личные сообщения, ротация аккаунтов/прокси и маскировка автоматизации по-прежнему отсутствуют.

## Новые возможности

### Аналитика

- tenant-scoped overview за выбранный диапазон;
- daily timeseries;
- delivery statuses и success rate;
- conversations, consent и handoff;
- candidate funnel;
- AI interactions/latency;
- integration outbox;
- campaign breakdown;
- A/B template delivery breakdown;
- обезличенный CSV без message body и contacts.

### Browser session control

- список активных sessions;
- текущая session отмечается отдельно;
- selective revoke устройства;
- revoke all other sessions;
- refresh token family/replay semantics сохранены;
- audit для security actions.

### Visual candidate flows

- versioned graph model;
- message/question/choice/handoff/end nodes;
- graph validation;
- field validation для age/phone/email/text;
- deterministic execution в inbound worker;
- active revision immutable;
- visual editor;
- drag-and-drop reordering;
- up/down fallback для mobile и accessibility.

### Safe destination onboarding

- TXT/CSV/TSV preview и apply;
- post-row result statuses;
- duplicate handling через savepoint;
- invite-link rejection;
- no auto-join;
- CSV export;
- discovery dialogs, уже доступных Bot API/MTProto connection;
- повторный gateway resolve выбранного dialog;
- permission evidence остаётся отдельным обязательным шагом.

### Individual destination windows

- IANA timezone назначения;
- weekdays;
- start/end time;
- night windows crossing midnight;
- nearest allowed UTC time;
- per-destination cooldown override;
- worker defer без сетевого запроса.

### Campaign editing and A/B templates

- editing draft/paused campaign;
- ordered destinations retained;
- clone to independent draft;
- schedule/route change resets approval;
- primary and secondary template;
- configurable B weight 1–99%;
- stable SHA-256 assignment by campaign/destination;
- exact preview and immutable job snapshot;
- analytics by delivery variant.

### PWA

- installable manifest and icons;
- offline static page;
- versioned shell cache;
- no caching for API, auth, webhook, metrics or non-GET;
- no personal data in offline content.

### ClamAV

- optional `clamd` TCP INSTREAM adapter;
- scan before storage;
- malware block with critical audit;
- fail-closed and explicit fail-open policy;
- no shell execution of uploaded files.

## Исправления и hardening

- тестовый Argon2 profile ускорен только для isolated test settings; production profile не ослаблен;
- campaign update корректно invalidates previous approval;
- destination window учитывается worker непосредственно перед send;
- bulk concurrent duplicate не оставляет SQL transaction broken;
- test/release script проверяет service worker syntax;
- pytest получает hard timeout;
- test output перенаправляется во временный файл, чтобы helper process не удерживал CI stdout pipe;
- MTProto client metadata и container/version metadata обновлены до 1.1.0.

## Database migrations

Новые revisions:

1. `7e31b486a5c9_v1_1_automation_flows.py`;
2. `c3f5a8d91b27_v1_1_destination_windows.py`;
3. `d8b4a210f6c3_v1_1_campaign_template_variants.py`.

Перед upgrade обязательны DB/storage backup и остановка worker.

```bash
alembic upgrade head
```

## QA

- 81 automated tests;
- coverage gate 65%; фактическое statement coverage выше 72%;
- Python compileall;
- SPA/service-worker JavaScript syntax;
- Alembic upgrade/check/downgrade/upgrade;
- HTTP login/CSRF/dashboard/logout smoke;
- isolated worker cycle;
- PWA no-sensitive-cache checks;
- tests для analytics, sessions, flows, import/discovery, windows, ClamAV, campaign editing и A/B.

Финальные числовые результаты находятся в `docs/QA_REPORT.md` и `BUILD_INFO.json`.

## Live-gates

Следующие проверки требуют ресурсов владельца:

- реальный Bot API token;
- `api_id/api_hash` и MTProto challenge;
- Telegram Business connected profile;
- пять destinations с разрешением;
- внешний AI/Google Sheets/S3 при использовании;
- reachable ClamAV daemon при включённом antivirus;
- production TLS/PostgreSQL/Redis/backup target.

Сценарий приёмки: `docs/PILOT_CHECKLIST.md`.
