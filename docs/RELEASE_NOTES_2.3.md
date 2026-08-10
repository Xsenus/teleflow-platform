# TeleFlow Platform 2.3.0 — Execution Fencing & Failover Assurance

Дата: **8 августа 2026 года**
Alembic head: `9b3d5f7a1c4e`

## Главное

Версия 2.3 закрывает риск split-brain при нескольких worker или двух площадках. Для каждой организации существует один active execution lease, монотонный fencing epoch и durable ledger каждой Telegram-попытки.

## Добавлено

- registry площадок с heartbeat, hostname, worker ID и версией;
- tenant execution lease со статусами active/draining/frozen;
- монотонный epoch при takeover и управляемом failover;
- stamping `execution_site_key` и `execution_epoch` в delivery jobs;
- отдельная таблица delivery attempts;
- durable состояния `prepared` и `network_started`;
- ручная сверка после аварии во время Telegram-вызова;
- failover request с независимым Owner/Admin approval;
- точная подтверждающая фраза;
- blocker snapshot для processing и uncertain deliveries;
- scheduler gate для standby-площадки;
- Safety Engine gate перед созданием Telegram gateway;
- Prometheus metrics и alerts;
- 30-й раздел панели «Active / Standby»;
- commissioning check execution fencing;
- migration `9b3d5f7a1c4e`.

## Исправлено

- повторный номер попытки после Slow Mode больше не переиспользуется;
- `attempt_count` соответствует числу фактически начатых сетевых попыток;
- отметка начала сети сохраняется вне основной незавершённой delivery-транзакции;
- падение после `NETWORK_STARTED` не приводит к автоматическому retry;
- ручная сверка атомарно инвалидирует старый epoch, поэтому разрешённый повтор не ждёт истечения старого TTL;
- revoke/cancel/controlled abort не уничтожают evidence неопределённой доставки.

## Failover-последовательность

```text
active site A
→ request failover to B
→ lease A: draining
→ verify no processing/uncertain attempts
→ independent approval
→ epoch N + 1
→ site B active
```

Автоматического failover нет. Смена площадки не используется для обхода FloodWait, anti-spam или auth restriction.

## Миграция

```bash
alembic upgrade head
alembic current
```

Ожидаемый head:

```text
9b3d5f7a1c4e
```

Проверяемый rollback:

```text
2.3 9b3d5f7a1c4e
→ downgrade
2.2 8a2c4e6f0b3d
→ upgrade
2.3 9b3d5f7a1c4e
```

Downgrade удаляет execution registry, failover requests, delivery-attempt ledger и execution-поля jobs. Перед downgrade необходимо завершить или вручную сверить все неопределённые попытки и остановить обе площадки.

## Конфигурация

```env
TELEFLOW_EXECUTION_FENCING_REQUIRED=true
TELEFLOW_EXECUTION_SITE_KEY=primary
TELEFLOW_EXECUTION_SITE_NAME=Основная площадка
TELEFLOW_EXECUTION_PRIMARY_SITE_KEY=primary
TELEFLOW_EXECUTION_LEASE_TTL_SECONDS=45
TELEFLOW_EXECUTION_SITE_HEARTBEAT_TTL_SECONDS=90
TELEFLOW_EXECUTION_REQUIRE_DISTINCT_FAILOVER_APPROVER=true
```

## QA

Итоговый release-gate:

```text
246/246 тестов пройдено
29 функциональных модулей
19 coverage-процессов
16 211 statements
3 270 missed
79,83% statement coverage
gate 65%
```

Дополнительно пройдены migration round-trip, HTTP/CSRF, worker cycle, audit-chain, master-key rotation, release asset validation и signed recovery smoke.

## Внешние границы

Для настоящей проверки active/standby нужны две отдельные инсталляции, общая production PostgreSQL, разные `site_key`, реальный Telegram test chat и контролируемое аварийное завершение worker. Эти инфраструктурные испытания не подменяются fake transport.
