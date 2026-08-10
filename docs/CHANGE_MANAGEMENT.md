# Change Management & Upgrade Assurance — TeleFlow 1.9

Версия 1.9 добавляет управляемый контур изменений для production-инсталляции. Он не выполняет обновление операционной системы, Docker image или базы «по кнопке» и не пытается автоматически откатывать сервер. Его задача — не дать оператору потерять контроль над публикациями во время изменения и сохранить проверяемую историю того, что было согласовано и проверено.

## Жизненный цикл

```text
draft
→ independent approval
→ maintenance mode (для upgrade/migration/infrastructure)
→ pre-change verification
→ in_progress
→ deployment выполняется оператором
→ post-change verification
→ completed
```

При проблеме активное изменение переводится в `failed`, после чего оператор выполняет записанный rollback plan. Черновик или утверждённое, но ещё не начатое изменение можно отменить.

## Change request

Сохраняются:

- тип: `upgrade`, `configuration`, `database_migration`, `infrastructure`;
- текущая и целевая версия;
- причина;
- описание риска;
- обязательный rollback plan;
- плановое окно;
- SHA-256 fingerprint согласованного содержимого;
- автор, согласовавший администратор, оператор запуска и завершения;
- timestamps и итоговый результат.

Автор **не может самостоятельно утвердить** свой change request. Для утверждения нужен другой Owner/Admin.

## Maintenance mode

Maintenance mode отделён от аварийного emergency stop. Он предназначен для запланированного изменения платформы.

При активном режиме:

- scheduler не создаёт новые запуски кампаний;
- ручные run/resume блокируются;
- Safety Engine блокирует уже арендованное delivery-задание до Telegram network call;
- веб-панель показывает глобальный warning banner;
- health/auth/audit/операционные API остаются доступными для диагностики.

Отключить maintenance mode нельзя, пока существует change request со статусом `in_progress`.

## Pre-change verification

Перед переводом изменения в работу проверяются:

1. Alembic current revision относительно head.
2. Maintenance mode для upgrade/database migration/infrastructure.
3. Отсутствие delivery jobs в состоянии `processing`.
4. Соответствие текущей версии ожидаемой версии change request.
5. Целостность tenant audit hash-chain.

В development/test auto-created SQLite schema даёт warning, но в production schema drift является blocker.

## Post-change verification

Перед `completed` проверяются:

1. Alembic current revision == head.
2. Фактическая версия приложения == target version, если она задана.
3. Целостность audit hash-chain.
4. Свежий worker heartbeat; в production его отсутствие является blocker.

Результат сохраняется как `deployment_verification_report` с checks, blockers, warnings, TTL и SHA-256 fingerprint.

## Что система намеренно не делает

TeleFlow не выполняет SSH-команды на чужом сервере, не скачивает произвольные release-архивы, не заменяет Docker image автоматически и не делает destructive database restore из веб-панели. Фактический deploy выполняется через утверждённый runbook. Такой подход оставляет административный доступ, ключи registry и rollback под контролем владельца инфраструктуры.

## API

```text
GET  /api/v1/changes
POST /api/v1/changes
POST /api/v1/changes/{id}/approve
GET  /api/v1/changes/{id}/verifications
POST /api/v1/changes/{id}/verify/{pre_change|post_change}
POST /api/v1/changes/{id}/start
POST /api/v1/changes/{id}/complete
POST /api/v1/changes/{id}/fail
POST /api/v1/changes/{id}/cancel
POST /api/v1/changes/maintenance/start
POST /api/v1/changes/maintenance/stop
```

## Рекомендуемый production upgrade

```text
1. Проверить Recovery Assurance и свежий restore drill.
2. Создать change request с rollback plan.
3. Получить независимое утверждение.
4. Включить maintenance mode.
5. Выполнить pre-change verification.
6. Создать/проверить backup по Recovery Assurance.
7. Выполнить deploy и Alembic upgrade штатными средствами инфраструктуры.
8. Запустить post-change verification.
9. При успехе завершить change request.
10. Отключить maintenance mode.
11. Выполнить commissioning/pilot readiness перед возобновлением live-нагрузки при существенном изменении.
```
