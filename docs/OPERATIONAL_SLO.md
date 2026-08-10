# Operational SLO & Incident Assurance

Версия: **TeleFlow Platform 2.2.0**
Дата: **7 августа 2026 года**

## 1. Назначение

Контур Operational SLO связывает наблюдаемость с фактическим допуском к Telegram-публикациям и production-изменениям. Он не ограничивается графиками: worker, scheduler, ручные API-запуски и Safety Engine используют одну и ту же актуальную оценку и блокируют действие до сетевого вызова, когда политика нарушена.

Контур предназначен для разрешённых публикаций. Он не является механизмом подбора «антибан-интервалов», обхода ограничений Telegram или автоматического продолжения после FloodWait/anti-spam.

## 2. Модель SLO

Политика хранится отдельно для каждой организации и включает:

- окно оценки в часах;
- целевую успешность доставки в basis points;
- минимальный объём выборки;
- максимальный возраст ожидающего задания;
- максимальный возраст heartbeat worker;
- допустимое количество `waiting_review`;
- допустимое количество открытых критических инцидентов;
- warning/critical thresholds расхода error budget;
- TTL оценки;
- отдельные gates для публикаций и управляемых изменений;
- автоматическое создание и разрешение SLO-инцидентов;
- подавление новых SLO-инцидентов во время утверждённого maintenance mode.

Изменение политики меняет её SHA-256 и автоматически делает прежний assessment неактуальным.

## 3. Расчёт доставки и error budget

В окно оценки входят завершённые delivery jobs:

```text
sent
failed
waiting_review
```

`waiting_review` считается неопределённой доставкой и расходует error budget, потому что автоматический retry запрещён до ручной сверки.

```text
eligible = sent + failed + waiting_review
success_rate = sent / eligible
allowed_failure_rate = 1 - target_success_rate
error_budget_consumed = observed_failures / allowed_failures
```

В базе `error_budget_consumed_bps=10000` означает расход **100%** бюджета. Значение может превышать 100%, если фактическое число ошибок больше допустимого.

При нулевой выборке система создаёт предупреждение, но не выдумывает success rate. Это позволяет провести первый контролируемый canary после проверки worker и остальных gates.

## 4. Проверки assessment

Каждый assessment содержит неизменяемый набор checks:

| Код | Проверка |
|---|---|
| `delivery_sample` | достаточность выборки |
| `delivery_error_budget` | расход error budget |
| `queue_age` | возраст самого старого pending/retry/processing job |
| `worker_heartbeat` | наличие и свежесть worker heartbeat |
| `unresolved_delivery_reviews` | количество неоднозначных доставок |
| `open_critical_incidents` | активные критические несистемные инциденты |

Статусы:

```text
passed
warning
blocked
```

Assessment сохраняет:

- временное окно;
- источник `manual` или `worker`;
- снимок политики;
- SHA-256 политики;
- фактические метрики;
- checks, blockers и warnings;
- TTL;
- SHA-256 fingerprint полного доказательства.

Assessment не обновляется задним числом. Следующая оценка создаёт новую запись.

## 5. Gates

### Публикации

Gate повторяется на трёх уровнях:

1. ручной `run-now` и `resume`;
2. scheduler до создания campaign run/jobs;
3. Safety Engine непосредственно перед созданием Telegram gateway.

При блокировке Telegram API/MTProto не вызывается. Safety Engine возвращает `SLO_GATE_BLOCKED`, а job не увеличивает счётчик сетевых попыток.

### Change management

Pre-change и post-change verification включают `operational_slo`. При включённом `gate_changes` отсутствие актуальной оценки, blocked assessment или превышение лимита открытых критических инцидентов блокирует production-изменение.

### Commissioning

Commissioning показывает отдельную проверку `operational_slo`. В production обязательный gate становится blocker; в development отключённый gate отображается как предупреждение или информационный результат.

## 6. Автоматическая оценка

Unified worker периодически запускает оценку под distributed lock:

```text
slo-evaluation
```

Интервал задаётся `TELEFLOW_SLO_AUTO_EVALUATE_MINUTES`. Один worker создаёт оценку, остальные процессы пропускают цикл. Для каждой активной организации worker при необходимости создаёт default policy, поэтому новый tenant не зависит от первого открытия страницы SLO. Ошибка одной организации изолируется savepoint и не отменяет оценки остальных tenants. Ручная оценка доступна Owner, Admin и Operator.

## 7. Управление инцидентами

Инциденты имеют статусы:

```text
open
acknowledged
mitigating
resolved
closed
```

Источники:

```text
manual
slo
delivery
worker
change
security
```

Публичный endpoint создания инцидента всегда фиксирует источник `manual`.
Системные источники `slo`, `delivery`, `worker`, `change` и `security`
зарезервированы за доверенными backend-контурами и не могут быть подменены
оператором через произвольное поле запроса.

Жизненный цикл:

```text
created
→ acknowledged
→ mitigation_started
→ resolved
→ closed
```

Разрешённый или закрытый инцидент можно повторно открыть. Комментарии создают отдельные события и не меняют статус.

Для каждого действия сохраняются:

- actor;
- timestamp;
- сообщение;
- structured payload;
- запись в audit hash-chain;
- уведомление для критических переходов.

## 8. Автоматические SLO-инциденты

Каждый blocked check получает стабильный dedup key:

```text
slo:<check_code>
```

Повторная оценка не создаёт копию активного инцидента, а обновляет ссылку на последний assessment. Оценки одной организации сериализуются блокировкой строки policy, поэтому одновременные API/worker циклы не должны создавать дубли. После восстановления показателя система может автоматически перевести инцидент в `resolved`, сохранив событие и audit trail.

Во время maintenance mode политика может подавлять создание **новых** SLO-инцидентов. Уже существующий инцидент всё равно разрешается автоматически после фактического восстановления показателя: режим обслуживания не используется для сокрытия восстановления или удержания ложной блокировки.

SLO-инциденты исключены из метрики `open_critical_incidents` внутри самого assessment, чтобы не возникал самоподдерживающийся цикл. Однако общий gate учитывает активные критические инциденты, если policy требует ручного разрешения.

## 9. Web/API

Раздел **«Надёжность и инциденты»** показывает:

- актуальность assessment;
- gates публикаций и изменений;
- delivery success;
- error budget;
- очередь и heartbeat;
- blockers/warnings;
- историю assessment;
- список инцидентов и полную event timeline.

Основные API:

```text
GET    /api/v1/operations/overview
GET    /api/v1/operations/slo-policy
PATCH  /api/v1/operations/slo-policy
GET    /api/v1/operations/slo-assessments
POST   /api/v1/operations/slo-assessments
GET    /api/v1/operations/incidents
POST   /api/v1/operations/incidents
GET    /api/v1/operations/incidents/{id}
PATCH  /api/v1/operations/incidents/{id}
GET    /api/v1/operations/incidents/{id}/events
POST   /api/v1/operations/incidents/{id}/acknowledge
POST   /api/v1/operations/incidents/{id}/mitigate
POST   /api/v1/operations/incidents/{id}/resolve
POST   /api/v1/operations/incidents/{id}/comment
POST   /api/v1/operations/incidents/{id}/close
POST   /api/v1/operations/incidents/{id}/reopen
```

Viewer имеет read-only доступ. Operator может оценивать SLO, создавать/вести активный инцидент и комментировать. Закрытие, повторное открытие и изменение policy доступны Owner/Admin.

## 10. Prometheus и Grafana

Добавлены метрики:

```text
teleflow_slo_latest_assessments{status}
teleflow_slo_error_budget_max_percent
teleflow_slo_stale_assessments
teleflow_open_incidents{severity}
```

Добавлены alerts:

```text
TeleFlowSLOBlocked
TeleFlowSLOAssessmentStale
TeleFlowErrorBudgetExhausted
TeleFlowCriticalIncidentOpen
```

Grafana dashboard показывает последние SLO-статусы, максимальный расход error budget и открытые инциденты.

## 11. Production-конфигурация

В production обязательно:

```text
TELEFLOW_SLO_GATE_REQUIRED=true
```

Основные параметры:

```text
TELEFLOW_SLO_AUTO_EVALUATE_MINUTES=15
TELEFLOW_SLO_ASSESSMENT_TTL_MINUTES=30
TELEFLOW_SLO_DEFAULT_EVALUATION_WINDOW_HOURS=24
TELEFLOW_SLO_DEFAULT_DELIVERY_SUCCESS_TARGET_BPS=9900
TELEFLOW_SLO_DEFAULT_MINIMUM_DELIVERY_SAMPLE_SIZE=10
TELEFLOW_SLO_DEFAULT_MAX_QUEUE_AGE_SECONDS=900
TELEFLOW_SLO_DEFAULT_MAX_WORKER_HEARTBEAT_AGE_SECONDS=120
TELEFLOW_SLO_DEFAULT_MAX_UNRESOLVED_DELIVERY_REVIEWS=0
TELEFLOW_SLO_DEFAULT_MAX_OPEN_CRITICAL_INCIDENTS=0
TELEFLOW_SLO_DEFAULT_ERROR_BUDGET_WARNING_PERCENT=50
TELEFLOW_SLO_DEFAULT_ERROR_BUDGET_CRITICAL_PERCENT=100
```

Warning threshold должен быть меньше critical threshold. Production runtime отказывается запускаться при отключённом обязательном gate.

## 12. Операционный runbook

При blocked assessment:

1. Не пытайтесь обходить gate через другой endpoint или worker.
2. Откройте checks и соответствующий инцидент.
3. При `worker_heartbeat` проверьте service/container и БД.
4. При `queue_age` остановите новые кампании и выясните причину задержки.
5. При `waiting_review` вручную проверьте целевые Telegram-чаты.
6. При расходе error budget изучите коды ошибок и Telegram restrictions.
7. Зафиксируйте mitigation и root cause.
8. Создайте новую SLO-оценку.
9. Убедитесь, что assessment актуален и gate пройден.
10. Только затем возобновляйте staged rollout или maintenance change.

Снижение thresholds только ради разблокировки без анализа причины считается изменением политики и оставляет audit trail; предыдущая оценка при этом теряет актуальность.
