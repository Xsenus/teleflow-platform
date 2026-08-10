# TeleFlow Platform 2.2.0 — Operational SLO & Incident Assurance

Дата релиза: **7 августа 2026 года**

## Основное

Версия 2.2 связывает фактическую надёжность платформы с допуском к публикациям и production-изменениям.

Добавлены:

- tenant-scoped SLO policy;
- неизменяемые assessments с TTL, policy hash и fingerprint;
- delivery success/error budget;
- контроль queue age, worker heartbeat и `waiting_review`;
- управляемый incident lifecycle и отдельная event history;
- автоматические deduplicated SLO incidents;
- SLO-gate в run-now/resume, scheduler, Safety Engine и change management;
- SLO-проверка в commissioning;
- периодическая оценка worker под distributed lock;
- 29-й раздел SPA «Надёжность и инциденты»;
- Prometheus metrics, alerts и новые Grafana panels;
- production hardening через `TELEFLOW_SLO_GATE_REQUIRED=true`;
- миграция Alembic `8a2c4e6f0b3d`.

## Безопасность

- blocked gate срабатывает до Telegram network call;
- policy change делает старый assessment неактуальным;
- uncertain delivery расходует error budget и не получает автоматический retry;
- критические SLO-события не создают бесконечные дубликаты;
- auto-resolve сохраняет историю, audit и уведомление;
- Viewer остаётся read-only;
- production не запускается при отключённом обязательном SLO gate.

## Совместимость

Обновление выполняется обычным Alembic round-trip:

```text
2.1 7f1b3d5e9a2c
→ 2.2 8a2c4e6f0b3d
```

Downgrade удаляет только таблицы Operational SLO/Incident Assurance. Исторические delivery jobs, кампании и Telegram credentials не изменяются.

## Новые таблицы

```text
slo_policies
slo_assessments
incidents
incident_events
```

## Внешние ограничения

Релиз не заявляет как пройденные реальные Telegram-публикации, MTProto login, Telegram Business webhook или production load test без credentials и инфраструктуры владельца. SLO не является гарантией отсутствия Telegram-ограничений и не используется для их обхода.

## QA

```text
234 automated tests passed
28 functional modules
18 isolated coverage processes
15 511 statements
3 149 missed statements
79,70% statement coverage
65% required gate
Alembic 2.2→2.1→2.2 round-trip: PASS
HTTP/CSRF/worker/audit/key-rotation/recovery smoke: PASS
```

Реальный Telegram live-pilot и production SLO evidence остаются внешними приёмочными gates.
