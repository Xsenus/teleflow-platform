# Execution Fencing & Failover Assurance

Версия: **TeleFlow Platform 2.3.0**
Alembic head: `9b3d5f7a1c4e`
Предыдущий head: `8a2c4e6f0b3d`

## Назначение

Контур Execution Fencing не позволяет двум worker-процессам или двум площадкам одновременно выполнять Telegram-вызовы для одной организации. Это особенно важно при зависшем процессе, потере связи с базой, перезапуске контейнера, переключении primary/standby и повторном запуске задания после неоднозначного сетевого результата.

Telegram Bot API и MTProto не принимают пользовательский fencing token. Поэтому защита построена на двух уровнях:

1. До сетевого вызова платформа проверяет активную площадку, владельца lease и монотонный epoch.
2. Строка lease блокируется транзакцией на всё время Telegram-вызова, а факт начала сети сохраняется отдельной durable-записью до отправки.

Если процесс остановился после фиксации `NETWORK_STARTED`, платформа не пытается угадывать результат и не выполняет автоматический повтор.

## Модель active/standby

Для каждой организации существует одна запись `execution_leases`:

```text
active_site_key
holder_worker_id
epoch
status
lease_expires_at
last_renewed_at
```

Площадки регистрируются в `execution_sites` и отправляют heartbeat с:

```text
site_key
display_name
worker_id
hostname
version
last_seen_at
```

Статусы lease:

```text
active
  новые delivery jobs могут получить lease;

draining
  новые Telegram-вызовы запрещены до завершения или отмены failover;

frozen
  аварийное административное состояние без сетевых вызовов.
```

## Fencing epoch

`epoch` — монотонное число. Оно увеличивается при:

- takeover после истёкшего lease;
- повторном запуске процесса с тем же текстовым worker ID после истечения TTL;
- управляемом переключении active-площадки;
- ручной сверке неоднозначной доставки перед разрешённым повтором.

Каждое задание сохраняет:

```text
execution_site_key
execution_epoch
```

Непосредственно перед Telegram-вызовом Safety Engine проверяет:

- execution fencing включён;
- lease существует;
- lease находится в `active`;
- текущая площадка совпадает с `active_site_key`;
- `holder_worker_id` совпадает с выполняющим worker;
- epoch задания совпадает с текущим epoch;
- lease ещё не истёк.

Несовпадение любого условия блокирует сеть до создания Telegram gateway.

## Durable delivery-attempt ledger

Каждая фактическая попытка имеет отдельную запись `delivery_attempts` с уникальным ключом:

```text
(job_id, attempt_number)
```

Жизненный цикл:

```text
prepared
→ network_started
→ sent | failed | uncertain
```

Дополнительные конечные состояния:

```text
abandoned
reconciled_not_sent
reconciled_skipped
```

### Последовательность безопасной отправки

1. Worker получает execution lease и epoch.
2. Job переводится в `processing`, счётчик попыток увеличивается один раз.
3. `DeliveryAttempt(prepared)` сохраняется и коммитится отдельно.
4. Delivery-транзакция блокирует строку execution lease через `SELECT ... FOR UPDATE`.
5. Перед самым Telegram-вызовом `network_started` сохраняется отдельной транзакцией.
6. Telegram gateway выполняет ровно один запрос.
7. Результат и attempt завершаются в основной delivery-транзакции.

Это разделяет два принципиально разных падения.

### Падение до начала сети

Если сохранился только `prepared`, сеть не начиналась. Stale-recovery переводит попытку в `abandoned`, а job может безопасно получить управляемый retry.

### Падение после начала сети

Если зафиксирован `network_started`, Telegram мог уже принять сообщение. Stale-recovery переводит попытку в `uncertain`, а job — в `waiting_review` с кодом:

```text
WORKER_CRASH_DURING_SEND
```

Автоматический retry запрещён.

## Ручная сверка

Для `DELIVERY_RESULT_UNCERTAIN` и `WORKER_CRASH_DURING_SEND` оператор выбирает один результат:

```text
confirmed_sent
  сообщение найдено в Telegram; указывается message ID;

confirmed_not_sent
  сообщение отсутствует; разрешается новая отдельная попытка;

skipped
  job закрывается без повторной отправки.
```

Перед разрешённым повтором старый execution epoch атомарно инвалидируется. Это снимает старый lease немедленно, но не ослабляет fencing: новый worker обязан получить новый epoch.

## Управляемый failover

Автоматического переключения площадок нет. Failover выполняется только через явный двухэтапный процесс:

```text
Owner/Admin A создаёт запрос
→ source lease получает draining
→ система проверяет blockers
→ другой Owner/Admin вводит точную фразу
→ epoch увеличивается
→ target становится active
```

Точная фраза:

```text
ПЕРЕКЛЮЧИТЬ НА <site_key>
```

При включённом `TELEFLOW_EXECUTION_REQUIRE_DISTINCT_FAILOVER_APPROVER=true` автор запроса не может подтвердить собственное переключение.

Failover блокируется при наличии:

- jobs в `processing`;
- попыток `network_started`;
- jobs `waiting_review` с неопределённым результатом;
- offline/disabled target site;
- открытого другого failover request.

Blocker snapshot сохраняется в запросе для оператора и аудита.

## Scheduler и worker

Scheduler создаёт runs/jobs только на active site. Standby-площадка может:

- запускать API и веб-панель;
- отправлять heartbeat;
- отображать состояние;
- принимать управляемый failover;
- выполнять несетевые фоновые операции, не связанные с Telegram-публикацией.

Она не создаёт Telegram delivery jobs и не получает execution lease для сети.

Worker heartbeat:

- обновляет `execution_sites`;
- продлевает принадлежащие worker lease;
- публикует site/epoch в heartbeat details;
- не захватывает tenant, уже обслуживаемый живым другим worker.

## API

```text
GET  /api/v1/execution/overview
GET  /api/v1/execution/sites
GET  /api/v1/execution/failovers
POST /api/v1/execution/failovers
POST /api/v1/execution/failovers/{id}/approve
POST /api/v1/execution/failovers/{id}/cancel
GET  /api/v1/execution/delivery-attempts
```

Чтение доступно аутентифицированным ролям организации. Создание, подтверждение и отмена failover доступны Owner/Admin. Все запросы изолированы по `organization_id`.

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

На standby-хосте меняются как минимум:

```env
TELEFLOW_EXECUTION_SITE_KEY=standby-eu-2
TELEFLOW_EXECUTION_SITE_NAME=Резервная площадка EU-2
TELEFLOW_EXECUTION_PRIMARY_SITE_KEY=primary
```

Обе площадки обязаны использовать одну production PostgreSQL-базу. SQLite подходит только для локального одиночного запуска и тестов; межхостовый fencing требует PostgreSQL row locks.

## Production-требования

Production runtime отклоняет конфигурацию, если:

- `TELEFLOW_EXECUTION_FENCING_REQUIRED=false`;
- site key пуст или некорректен;
- обязательное разделение утверждения failover отключено в production Compose;
- площадки используют разные базы данных;
- часы хостов имеют значительный drift.

Рекомендуется:

- PostgreSQL с надёжным quorum/managed HA;
- NTP/chrony на всех площадках;
- уникальный `TELEFLOW_EXECUTION_SITE_KEY` на площадку;
- одинаковая версия приложения и миграций;
- отдельные worker process IDs;
- Prometheus alerts и ручной runbook failover.

## Наблюдаемость

Prometheus экспортирует:

```text
teleflow_execution_leases{status}
teleflow_execution_stale_leases
teleflow_execution_open_failovers
teleflow_execution_uncertain_attempts
teleflow_execution_offline_sites
```

Alerts:

```text
TeleFlowExecutionLeaseStale
TeleFlowFailoverPending
TeleFlowExecutionAttemptUncertain
TeleFlowExecutionSiteOffline
```

## Ограничения

Execution fencing существенно снижает риск дублирующей отправки, но не превращает Telegram в транзакционный ресурс. Если процесс погиб после того, как Telegram принял запрос, но до локального подтверждения, результат остаётся неопределённым. Единственно безопасное поведение — остановка и ручная сверка, что и реализовано.

Контур не предназначен для обхода Telegram FloodWait, anti-spam ограничений или правил групп. Failover не используется для продолжения рассылки после ограничения аккаунта.

## Проверка

```bash
python scripts/run_pytest.py -q tests/test_execution_fencing.py
./scripts/qa_release.sh
```

Проверяются:

- один active worker на организацию;
- запрет standby scheduler;
- takeover только после expiry и с новым epoch;
- блокировка старого worker;
- монотонные attempt numbers после Slow Mode;
- безопасный retry после `prepared`;
- ручная сверка после `network_started`;
- независимое подтверждение failover;
- сохранение blocker snapshot;
- RBAC и production-config gates;
- migration round-trip 2.3↔2.2.
