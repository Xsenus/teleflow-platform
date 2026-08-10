# TeleFlow Platform 2.5.0 — Capacity & Backpressure Assurance

## Основные изменения

Версия 2.5 добавляет tenant-level admission control и durable dispatch reservation. Крупная кампания, staged batch или ручной retry больше не могут бесконтрольно заполнить очередь либо начать больше сетевых Telegram-попыток, чем допускает политика организации.

### Добавлено

- tenant-scoped CapacityPolicy;
- immutable CapacityAssessment с policy SHA-256 и TTL;
- projected queue/run admission до создания jobs;
- повторная транзакционная проверка scheduler;
- ready-budget для staged rollout;
- dispatch precheck до gateway и attempt counter;
- durable `prepared` reservation против concurrent workers;
- minute/hour network-start budgets;
- estimated drain time с учётом connection interval и rate-window;
- periodic worker assessments;
- capacity controls и Alembic head включены в active/standby runtime fingerprint;
- 32-й раздел панели «Нагрузка и лимиты»;
- Prometheus metrics, alerts и Grafana panels;
- production-required capacity gate;
- Alembic head `bd5f7a9c3e6f`.

### Исправленные алгоритмические риски

- ready saturation теперь учитывается отдельно от общего active limit;
- точное достижение rate limit считается заполненным budget;
- конкурентная reservation после Safety precheck останавливается до Telegram gateway;
- partial PATCH не может создать противоречивую итоговую policy;
- manual retry проверяется до изменения terminal job;
- confirmed-not-sent reconciliation проверяет capacity до смены fencing epoch;
- transient rate exhaustion откладывает dispatch, но не отвергает bounded future queue;
- пустая очередь не получает фиктивный drain time из-за заполненного rate-window.

### Безопасность

- production требует `TELEFLOW_CAPACITY_ASSURANCE_REQUIRED=true`;
- RBAC и tenant isolation применяются ко всем policy/evidence endpoints;
- failure до network start не расходует Telegram attempt;
- durable attempt ledger сохраняет доказательство отсутствия сетевого вызова;
- Capacity Assurance не содержит механик повышения лимитов Telegram или обхода anti-spam.

### База данных

```text
2.4 ac4e6f8b2d5f
→ 2.5 bd5f7a9c3e6f
```

Миграция добавляет таблицы:

```text
capacity_policies
capacity_assessments
```

### Обновление 2.4 → 2.5

1. Создать проверенный backup и recovery receipt.
2. Перевести платформу в maintenance mode.
3. Обновить код и зависимости.
4. Выполнить `alembic upgrade head`.
5. Проверить head `bd5f7a9c3e6f`.
6. Настроить Capacity Policy сначала в monitor-only режиме.
7. Выполнить ручной assessment и нагрузочный dry-run.
8. Включить admission/dispatch gates.
9. Выполнить canary на собственной служебной группе.
10. Завершить change request только после post-change verification.

Полное описание: [CAPACITY_ASSURANCE.md](CAPACITY_ASSURANCE.md).
