# TeleFlow Platform 1.4.0 — Production Pilot

Дата релиза: 6 августа 2026 года.

## Основное

Версия 1.4 добавляет отдельный production-pilot control plane поверх approval, preflight и staged rollout. Цель релиза — исключить запуск с просроченной проверкой назначения, неготовым worker, неактуальным подключением или во время заранее заданного операционного запрета.

## Добавлено

### Production Pilot readiness

- сохраняемые отчёты готовности с TTL;
- fingerprint кампании и повторная проверка при запуске;
- blockers/warnings/passed checks;
- проверка организации, worker, подключения, утверждения, preflight, approver capacity, TOTP, caps и staged rollout;
- обязательный gate для `run-now`, `resume` и scheduler;
- audit и критическое уведомление при блокировке автоматического запуска.

### История проверки назначений

- `validated_at` и `validation_expires_at`;
- immutable validation records;
- ручная и пакетная проверка до 50 назначений;
- остановка оставшейся части пакета после Telegram retry-after;
- отдельные статусы `passed`, `failed`, `write_forbidden`, `deferred`;
- `CHAT_WRITE_FORBIDDEN` отключает назначение;
- старые `validated=true` мигрируются с сохранением смысла через `updated_at`.

### Операционный календарь

- запреты уровня organization/connection/destination;
- разовые UTC-окна;
- еженедельные IANA-timezone окна;
- корректная работа интервалов через полночь;
- Safety Engine откладывает job до окончания окна без Telegram network call;
- CRUD, evaluation API, audit и Web UI.

### Веб-панель

- новый 22-й раздел **Production Pilot**;
- сводка readiness;
- список назначений, требующих внимания;
- история валидаций;
- пакетная повторная проверка;
- календарь операционных запретов;
- просмотр подробного readiness report.

## Исправлено

- счётчик delivery attempts больше не увеличивается дважды за один фактический запрос;
- пользовательское название назначения сохраняется при первичном bulk/create onboarding;
- `write_forbidden` больше не теряется как общий `failed`;
- PATCH blackout очищает поля предыдущего scope/kind и повторно валидирует объединённую модель;
- условие сообщения глобального publishing switch сделано однозначным.

## Миграция

Новый Alembic head:

```text
c9e1f3a5b7d9
```

Поддерживается проверенный round-trip:

```text
1.3 b7c9d2e4f6a8
→ 1.4 c9e1f3a5b7d9
→ 1.3 b7c9d2e4f6a8
→ 1.4 c9e1f3a5b7d9
```

## Конфигурация

```env
TELEFLOW_DESTINATION_VALIDATION_TTL_HOURS=168
TELEFLOW_CONNECTION_HEALTH_TTL_HOURS=24
TELEFLOW_READINESS_TTL_MINUTES=30
TELEFLOW_PILOT_READINESS_REQUIRED=true
TELEFLOW_PILOT_STAGED_THRESHOLD=5
```

В development-примере обязательный readiness gate выключен для быстрого fake-mode знакомства. В production-примере он включён и является частью security validation.

## Совместимость

- Python 3.12+;
- PostgreSQL для production;
- SQLite для локального fake-mode и тестов;
- существующие 1.3 кампании требуют повторной проверки назначений и нового readiness report перед запуском при включённом gate.

## QA

Финальный QA: **121 тест**, **77,02% statement coverage**, чистая Alembic schema, round-trip 1.4→1.3→1.4, HTTP smoke, worker once, audit-chain verification и master-key rotation dry-run. Полные результаты находятся в `docs/QA_REPORT.md` и `BUILD_INFO.json`.

## Live-gates

Релиз не выдаёт за выполненные реальные публикации без Bot token, `api_id/api_hash`, авторизованной MTProto-сессии и разрешённых тестовых групп. Docker image build, production TLS, PostgreSQL/Redis, S3, ClamAV и restore drill должны быть проверены на целевом сервере.
