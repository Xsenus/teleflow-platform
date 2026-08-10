# Capacity & Backpressure Assurance 2.5

## Назначение

Контур Capacity Assurance ограничивает объём очереди и скорость начала сетевых Telegram-вызовов на уровне организации. Он не пытается повысить пропускную способность обходом ограничений Telegram. Его задача обратная: не допустить, чтобы крупная кампания, ручной retry или staged rollout создали нагрузку, которую worker, Telegram-подключения и операторская команда не успевают безопасно обработать.

Основной принцип:

```text
Admission control
→ durable queue
→ ready-release control
→ Safety Engine precheck
→ durable dispatch reservation
→ Telegram gateway
```

Проверки повторяются в нескольких слоях. Решение веб-интерфейса или предварительный assessment не считается достаточным разрешением для сетевого вызова.

## Tenant-scoped policy

Каждая организация имеет собственную политику:

- максимальное количество активных jobs;
- максимальное количество ready jobs;
- максимальное количество processing jobs;
- максимальное количество одновременно активных campaign runs;
- максимальный размер одного run;
- максимальное количество начатых сетевых попыток за минуту;
- максимальное количество начатых сетевых попыток за час;
- максимальное прогнозируемое время опустошения очереди;
- warning-порог загрузки;
- admission-block порог загрузки;
- TTL assessment;
- независимые флаги admission и dispatch enforcement.

Инварианты защищены одновременно схемой API, доменным сервисом и ограничениями базы данных:

```text
processing ≤ ready ≤ active
jobs_per_run ≤ active
minute_rate ≤ hour_rate
warning_percent < admission_block_percent
```

Частичное PATCH-изменение повторно проверяет итоговую строку целиком. Нельзя сохранить противоречивую политику, изменив одно поле поверх уже существующих значений.

## Capacity snapshot

Перед оценкой система собирает один согласованный снимок:

- active, ready, processing и held jobs;
- active campaign runs;
- фактические сетевые старты за минуту и час;
- незавершённые durable reservations `prepared`;
- projected jobs/runs для запрашиваемой операции;
- индивидуальный интервал Telegram connection;
- ближайшее освобождение rate-window;
- оценку времени обработки очереди;
- максимальную загрузку по независимым измерениям.

Загрузка очереди рассчитывается не только по общему числу active jobs. Используется наиболее насыщенное измерение:

```text
max(
  active_jobs / max_active_jobs,
  ready_jobs / max_ready_jobs,
  active_runs / max_active_runs
)
```

Поэтому почти заполненная ready-очередь не скрывается большим лимитом active jobs.

## Immutable assessments

Assessment сохраняет:

- источник: `manual`, `worker`, `admission` или `dispatch`;
- снимок политики;
- SHA-256 политики;
- метрики;
- checks;
- blockers и warnings;
- projected counts;
- estimated drain time;
- utilization;
- fingerprint;
- срок действия.

Изменение политики не переписывает старое evidence. Оно делает assessment неактуальным по несовпадающему policy SHA-256.

Assessment может иметь статус `blocked` из-за временно заполненного rate-window, но admission bounded-очереди при этом может быть разрешён. Немедленная отправка всё равно останется заблокированной Safety Engine до освобождения окна.

## Admission control

Admission выполняется:

- при `run-now`;
- при возобновлении кампании;
- в scheduler перед созданием run;
- повторно в транзакции перед созданием jobs;
- при ручном retry;
- при подтверждённом повторе после неопределённой доставки.

Проверяются projected active jobs, projected ready jobs, размер run, активные runs, utilization и estimated drain time.

Если admission запрещён:

- `CampaignRun` не создаётся;
- `DeliveryJob` не создаются;
- существующее terminal job при retry не изменяется;
- fencing epoch не инвалидируется;
- Telegram gateway не создаётся.

Monitor-only режим сохраняет assessment и показывает blocker, но не запрещает admission. В production обязательный capacity gate включён принудительно.

## Ready-release control

Staged rollout физически хранит будущие задания в состоянии `held`. Перед раскрытием следующего пакета система отдельно проверяет ready budget.

Если пакет переполнит ready-очередь:

- задания остаются `held`;
- checkpoint сохраняется;
- следующий пакет не публикуется;
- Telegram API не вызывается.

Проверка выполняется как при ручном checkpoint, так и при автоматическом переходе между пакетами.

## Dispatch precheck

Safety Engine выполняет precheck непосредственно перед подготовкой сетевой попытки. Проверяются:

- processing limit;
- фактические сетевые старты за минуту;
- фактические сетевые старты за час;
- durable `prepared` reservations других workers.

Точная граница считается заполненной:

```text
count >= limit → defer
```

При отказе:

- gateway не создаётся;
- `attempt_count` не увеличивается;
- Telegram message ID не появляется;
- job остаётся управляемо отложенным.

## Durable dispatch reservation

Precheck сам по себе не защищает от гонки двух workers. Поэтому после перевода job в `processing` создаётся durable `DeliveryAttempt(status=prepared)`, затем выполняется финальная проверка под блокировкой строки CapacityPolicy.

Текущая reservation уже входит в счётчик. Поэтому финальная проверка допускает ровно один собственный slot и блокирует следующую конкурирующую reservation.

Если другой worker занял лимит между precheck и финальной проверкой:

- текущая attempt переводится в `abandoned`;
- `network_started_at` остаётся пустым;
- `telegram_call_started=false` сохраняется в details;
- job возвращается в `pending`;
- gateway не создаётся.

Номер attempt не переиспользуется. Это сохраняет монотонный ledger и позволяет доказать, что сетевой вызов не начинался.

Блокировка CapacityPolicy не удерживается во время Telegram network call. Межхостовый порядок сетевых вызовов дополнительно защищён Execution Fencing 2.3 через authoritative PostgreSQL.

## Rate windows и drain estimate

Оценка времени обработки учитывает:

- минимальный интервал подключения;
- tenant minute/hour budgets;
- уже заполненное rate-window;
- ближайшее время, когда самая старая попытка выйдет из окна;
- количество projected active jobs.

Если active queue пуста, временно заполненное rate-window не создаёт фиктивное drain time. Если очередь существует, ожидание освобождения окна добавляется к прогнозу.

## Runtime compatibility

Capacity Assurance входит в critical runtime fingerprint active/standby площадок. Сравниваются обязательность gate, интервалы оценки, TTL, все default limits и retry delay. Площадка с другим capacity-профилем или старым Alembic head не допускается к failover.

## Worker evaluation

Worker периодически создаёт assessments для активных организаций:

- используется distributed lock;
- каждая организация обрабатывается отдельно;
- assessment не дублируется внутри настроенного интервала;
- ошибка одной организации не должна останавливать остальные tenant assessments.

## API

```text
GET   /api/v1/capacity/overview
GET   /api/v1/capacity/policy
PATCH /api/v1/capacity/policy
GET   /api/v1/capacity/assessments
POST  /api/v1/capacity/assessments
```

Owner/Admin изменяют политику и создают ручную оценку. Viewer имеет только чтение в пределах своей организации.

## Monitoring

Prometheus metrics:

```text
teleflow_capacity_latest_assessments{status}
teleflow_capacity_queue_utilization_max_percent
teleflow_capacity_estimated_drain_seconds_max
teleflow_capacity_stale_assessments
teleflow_capacity_backpressure_organizations
```

Alerts:

```text
TeleFlowCapacityBackpressureActive
TeleFlowCapacityAssessmentStale
TeleFlowCapacityDrainTimeHigh
```

Grafana показывает backpressure organizations, stale assessments, максимальную загрузку очереди и прогноз drain time.

## Production settings

```env
TELEFLOW_CAPACITY_ASSURANCE_REQUIRED=true
TELEFLOW_CAPACITY_AUTO_EVALUATE_MINUTES=5
TELEFLOW_CAPACITY_ASSESSMENT_TTL_MINUTES=15
TELEFLOW_CAPACITY_DEFAULT_MAX_ACTIVE_JOBS=500
TELEFLOW_CAPACITY_DEFAULT_MAX_READY_JOBS=100
TELEFLOW_CAPACITY_DEFAULT_MAX_PROCESSING_JOBS=5
TELEFLOW_CAPACITY_DEFAULT_MAX_ACTIVE_RUNS=20
TELEFLOW_CAPACITY_DEFAULT_MAX_JOBS_PER_RUN=100
TELEFLOW_CAPACITY_DEFAULT_MAX_NETWORK_STARTS_PER_MINUTE=30
TELEFLOW_CAPACITY_DEFAULT_MAX_NETWORK_STARTS_PER_HOUR=500
TELEFLOW_CAPACITY_DEFAULT_MAX_ESTIMATED_DRAIN_SECONDS=21600
TELEFLOW_CAPACITY_DEFAULT_WARNING_UTILIZATION_PERCENT=70
TELEFLOW_CAPACITY_DEFAULT_ADMISSION_BLOCK_UTILIZATION_PERCENT=90
TELEFLOW_CAPACITY_RETRY_DELAY_SECONDS=30
```

Значения являются безопасными исходными ограничениями платформы, а не обещанием отсутствия Telegram-ограничений. Для live-среды они должны быть уменьшены или подтверждены пилотом владельца.

## Проверенные негативные сценарии

Автоматические тесты подтверждают:

- противоречивую политику нельзя сохранить через API или напрямую в БД;
- насыщение ready-очереди не скрывается большим active limit;
- oversized run блокируется до создания jobs;
- scheduler повторно проверяет изменившуюся политику;
- точный minute limit блокирует gateway;
- concurrent durable reservations не могут занять один slot дважды;
- race после Safety precheck ловится финальной reservation;
- staged batch остаётся held при нехватке ready budget;
- ручной retry остаётся атомарным;
- временный rate exhaustion не отвергает bounded queue, но откладывает dispatch;
- assessment устаревает после изменения policy;
- RBAC и tenant isolation соблюдаются;
- production не запускается с отключённым обязательным gate;
- Prometheus alerts и Grafana panels входят в release assets.

## Ограничения приёмки

Локальные тесты подтверждают алгоритмы и отсутствие известных дефектов в смоделированных сценариях. Фактические значения capacity policy должны быть приняты после load-теста на целевых PostgreSQL/Redis/worker-хостах и контролируемой Telegram-canary. Capacity Assurance не заменяет правила Telegram и не является механизмом обхода FloodWait, SlowMode или anti-spam.
