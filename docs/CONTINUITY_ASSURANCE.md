# Continuity Assurance — TeleFlow Platform 2.4

Версия: **2.4.0**
Alembic head: `ac4e6f8b2d5f`
Предыдущий head: `9b3d5f7a1c4e`

## Назначение

Continuity Assurance подтверждает, что резервная площадка TeleFlow не просто зарегистрирована, а действительно совместима с active-площадкой и может быть безопасно введена в работу с последующим возвратом на исходную площадку.

Контур не предназначен для обхода Telegram-ограничений. Failover не снимает FloodWait, anti-spam, auth restriction и запреты назначения. Все Telegram-публикации продолжают проходить Execution Fencing, Safety Engine, SLO, permission, cooldown и blackout-gates.

## Режимы учения

### Simulation

Simulation проверяет конфигурацию и workflow без изменения execution lease:

```text
создание draft
→ фиксация policy/runtime snapshot
→ запуск simulation
→ evidence network_calls=0
→ независимая приёмка
```

Гарантии simulation:

- Telegram gateway не создаётся;
- Bot API и MTProto не вызываются;
- execution lease не изменяется;
- fencing epoch не увеличивается;
- failover request не создаётся;
- evidence содержит `network_calls: 0`, `lease_mutated: false` и `failover_requests_created: 0`.

Simulation полезна для локальной проверки интерфейса и governance, но не закрывает policy с `require_live_drill=true`.

### Live drill

Live-drill использует production-механизм controlled failover:

```text
Primary active, epoch N
→ Owner/Admin создаёт drill
→ runtime compatibility check
→ failover request Primary → Standby
→ независимый Owner/Admin подтверждает переключение
→ Standby active, epoch N+1
→ оператор запрашивает failback
→ независимый Owner/Admin подтверждает Standby → Primary
→ Primary active, epoch N+2
→ evidence + RTO
→ независимая приёмка
```

Автоматического failover и автоматического failback нет. Это принципиальная защита от split-brain и повторной отправки после неопределённого сетевого результата.

## Runtime evidence

Каждый heartbeat площадки сохраняет только безопасные операционные признаки:

- версия приложения;
- environment;
- текущая Alembic revision;
- ожидаемый migration head;
- `schema_current`;
- SHA-256 критической конфигурации;
- SHA-256 release payload;
- общий runtime fingerprint;
- worker ID, hostname и время проверки.

В runtime evidence не включаются Bot API tokens, `api_hash`, StringSession, JWT/master keys, TOTP secrets, AI/S3/Google credentials и приватные ключи.

Совместимость требует совпадения версии, migration head, критической конфигурации и release payload, а также свежего heartbeat и актуальной схемы обеих площадок.

## Политика организации

Для каждой организации хранится одна continuity policy:

```text
enabled
require_live_drill
max_rto_seconds
evidence_valid_days
require_distinct_signoff
```

Изменение policy создаёт новый fingerprint и аннулирует незавершённые учения, начатые по старым критериям. Принятое evidence по старой policy перестаёт удовлетворять compliance-gate.

Production требует:

```env
TELEFLOW_CONTINUITY_ASSURANCE_REQUIRED=true
TELEFLOW_CONTINUITY_REQUIRE_DISTINCT_SIGNOFF=true
TELEFLOW_CONTINUITY_DEFAULT_REQUIRE_LIVE_DRILL=true
TELEFLOW_CONTINUITY_DEFAULT_MAX_RTO_SECONDS=300
TELEFLOW_CONTINUITY_DEFAULT_EVIDENCE_VALID_DAYS=30
TELEFLOW_CONTINUITY_SYNC_INTERVAL_SECONDS=15
```

## Hash-linked история

Каждое состояние записывается отдельным событием:

```text
created
simulation_completed
failover_requested
target_active
failback_requested
primary_restored
signed_off
cancelled
invalidated
failed
```

Для события защищаются:

- organization ID;
- drill ID;
- sequence;
- event type;
- actor user ID;
- payload;
- previous hash;
- created time.

Проверка выявляет удаление, перестановку, межорганизационный перенос и изменение содержимого. Простого пересчёта одного hash недостаточно: sign-off дополнительно сверяет evidence с authoritative полями drill и hash истории, проверенной до terminal event.

## Evidence и RTO

Simulation evidence фиксирует отсутствие сетевой активности. Live evidence связывает:

- source/target site;
- source/target/return epoch;
- failover/failback request IDs;
- policy SHA-256;
- runtime snapshot;
- timestamps;
- фактический RTO;
- признак использования fenced production failover.

Evidence получает собственный SHA-256. Перед sign-off система повторно проверяет:

1. Статус drill.
2. Актуальность policy fingerprint.
3. Семантическое соответствие evidence полям БД.
4. Целостность event chain.
5. Соответствие live/simulation policy.
6. RTO.
7. Независимость принимающего пользователя.
8. Возврат active lease на исходную площадку.

## Фоновая синхронизация

Worker запускает `synchronize_open_drills` с распределённой блокировкой `continuity-sync`.

Фоновый процесс:

- читает только незавершённые live-drills;
- проецирует уже завершённые failover requests;
- не создаёт новый failover;
- не выполняет Telegram-вызов;
- обрабатывает каждый drill в отдельном database savepoint;
- ошибка одной организации не откатывает успешно синхронизированные организации;
- фиксирует audit event только при реальном изменении состояния.

## Commissioning и наблюдаемость

Commissioning включает отдельный check `continuity_assurance`. В production отсутствие актуального принятого live-drill или совместимой standby-площадки блокирует ввод в эксплуатацию.

Prometheus экспортирует:

```text
teleflow_continuity_drills{mode,status}
teleflow_continuity_compliance{state}
teleflow_continuity_last_rto_seconds
teleflow_continuity_stale_evidence
teleflow_continuity_rto_breaches
```

Alerts:

```text
TeleFlowContinuityEvidenceStale
TeleFlowContinuityDrillRunning
TeleFlowContinuityRTOBreached
TeleFlowContinuityBlocked
```

## API

```text
GET   /api/v1/continuity/overview
GET   /api/v1/continuity/policy
PATCH /api/v1/continuity/policy

GET   /api/v1/continuity/drills
POST  /api/v1/continuity/drills
GET   /api/v1/continuity/drills/{id}
POST  /api/v1/continuity/drills/{id}/start
POST  /api/v1/continuity/drills/{id}/failback
POST  /api/v1/continuity/drills/{id}/signoff
POST  /api/v1/continuity/drills/{id}/cancel
GET   /api/v1/continuity/drills/{id}/events
GET   /api/v1/continuity/drills/{id}/verify
```

Чтение доступно авторизованным пользователям своей организации. Изменения доступны только Owner/Admin и дополнительно защищены CSRF, tenant isolation и audit-chain.

## Запрещённые переходы

Система блокирует:

- параллельные незавершённые учения одной организации;
- запуск после изменения active site или epoch;
- несовместимую либо просроченную standby;
- отмену live-drill после фактического переключения без failback;
- изменение policy, пока active lease остался на target site;
- самостоятельную приёмку автора при включённом four-eyes;
- принятие simulation при live-only policy;
- принятие evidence с превышенным RTO;
- принятие повреждённой или семантически подменённой истории;
- принятие PASSED-статуса без sign-off metadata.

## Реальная приёмка

Локальные автоматические тесты подтверждают состояние машины и транзакционные инварианты. Production-приёмка требует двух отдельных хостов, общей PostgreSQL, разных `TELEFLOW_EXECUTION_SITE_KEY`, реального worker heartbeat и controlled failover/failback. Учение проводится только в собственном тестовом Telegram-чате или в группах с явным разрешением.
