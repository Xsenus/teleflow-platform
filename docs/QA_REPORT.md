# QA report — TeleFlow Platform 2.5.0

Дата актуального локального аудита: **10 августа 2026 года**

Alembic head: `bd5f7a9c3e6f`

Предыдущий head: `ac4e6f8b2d5f`

## Итог актуального дерева

```text
Собрано тестов:              680
Пройдено:                    678
Ожидаемо пропущено:            2 (Unix-only на Windows)
Упало:                       0
Функциональных модулей:      71
Statements:                  18 001
Missed statements:           1 175
Statement coverage:          93,47%
Обязательный gate:           80%
Полный прогон:               284,3 секунды + отдельная manifest-проверка
Coverage-прогон:             проверенная базовая матрица + 693,2 секунды новых ветвей
```

Основной прогон расширенного дерева подтвердил 677 passed и 2 ожидаемых Windows skip при
единственной временно исключённой manifest-проверке. После обновления отчётов и штатной
регенерации `MANIFEST.sha256` отдельная проверка довела итог до 678 passed и 2 skipped.
Coverage объединён из ранее проверенной полной матрицы и изолированных coverage-сессий новых
API-ветвей; полное функциональное выполнение текущего дерева отдельно проверило teardown.
Release-QA запускает 21 изолированный coverage-shard и заново строит итоговый файл в Linux CI.

Каждый тест получает файловую копию один раз инициализированной эталонной SQLite-базы.
Это сохраняет полную изоляцию данных и production-путь `create_all`, но исключает повторное
создание схемы и Argon2 bootstrap-владельца. Время полного Windows-прогона уменьшилось с
802 до 363,3 секунды (примерно на 55%) на предыдущем монолитном baseline. Текущее расширенное
дерево без отдельно проверенного manifest-теста завершило Windows-прогон за 284,3 секунды.
Linux release-QA по-прежнему запускает модули и
coverage-shard изолированно.

## Документация функций

Автоматический quality-gate анализирует Python AST и именованные определения JavaScript:

```text
Python functions:            1 913
JavaScript named functions:    156
Функций без пояснения:           0
```

Проверка охватывает `app`, `scripts`, `migrations`, `tests`, SPA и service worker. Для каждого Python callable требуется русскоязычный docstring, для именованной JavaScript-функции — русскоязычный JSDoc или непосредственно предшествующий поясняющий комментарий. Gate реализован в `scripts/check_function_docs.py`, `tests/test_function_documentation.py` и `scripts/validate_release_assets.py`.

## Новые алгоритмические проверки 2.5

Отдельный inbound lifecycle-пакет проверяет idempotency и конкурентную unique-гонку, повторное
подключение Telegram Business, позднее назначение automation policy существующему диалогу,
редактирование и удаление сообщений, consent/rate/daily-cap ограничения, AI fallback/handoff и
валидацию операторских ответов. `app/services/inbound.py` покрыт на 100% statements.

Supply-chain edge-пакет проверяет строгий контракт scanner report, timestamp/timezone, dependency
grammar и pinning, production-policy, immutable report/policy/SBOM hashes, состояния подписей,
tenant isolation, transparency sequence/hash/payload binding и change-request release gates.
`app/services/supply_chain.py` покрыт на 100% statements.

Configuration bundle edge-пакет проверяет ZIP metadata/path/encryption/size, подпись export/import,
media SHA/antivirus/fail-open/cleanup, все reference maps и безопасные defaults для каждого типа
импортируемой сущности. Удалена недостижимая повторная duplicate-path проверка; case-insensitive
защита сохранена. `app/services/config_bundles.py` покрыт на 100% statements.

Security runtime-пакет проверяет outbound SSRF/DNS/IP guards, все media magic signatures и UTF-8
streaming, upload rollback, cookie CSRF, IP allowlist/proxy handling, tenant authentication и
media deletion. Дополнительные пакеты исчерпывают Safety Engine dispatch gates, master-key
rotation/restore и continuity API lifecycle. Все семь затронутых production-модулей покрыты на
100% statements.

Privacy и destination edge-пакеты проверяют весь lifecycle запросов на экспорт/удаление,
retention и fail-closed расшифровку, все поддерживаемые форматы и локализованные поля импорта,
ограничения размера, разрешений и временных окон, ручную и пакетную Telegram-валидацию,
flood-wait с отложенными строками, конкурентную unique-гонку и безопасное удаление. Модули
`app/services/privacy.py`, `app/services/destination_bulk.py` и `app/api/destinations.py`
покрыты на 100% statements.

Flow/scheduler edge-пакеты проверяют повреждённые и зацикленные графы, handoff и повторный ввод,
валидацию и шифрование полей анкеты, stale approval, pilot/SLO/capacity/execution gates,
savepoint при duplicate-run, capacity race, истечение approvals и пустые one-shot/recurring
запуски. `app/services/flows.py` и `app/services/scheduler.py` покрыты на 100% statements.

Delivery/automation API edge-пакеты проверяют CRUD-конфликты policy/flow/provider, IANA timezone,
allowlist и SSRF, шифрование provider API key, health probe failures, execution lease/fence,
durable PREPARED/NETWORK_STARTED attempts, bounded transient retry, auth/permanent/internal errors,
staged capacity checkpoint, отсутствие chat_id и исчезновение media-файла перед отправкой.
Исправлена 500-ошибка PATCH flow при обновлении definition. `app/api/automation.py` и
`app/services/delivery.py` покрыты на 100% statements.

`tests/test_capacity_backpressure.py` и связанные regression-модули проверяют:

- safe defaults и создание tenant CapacityPolicy;
- ordered limits `processing ≤ ready ≤ active`;
- отказ противоречивого PATCH и direct database write;
- использование самого насыщенного измерения очереди, а не только active jobs;
- oversized run блокируется до создания CampaignRun и DeliveryJob;
- scheduler повторно проверяет policy после `run-now`;
- monitor-only policy сохраняет blocker, но не притворяется enforced gate;
- временно заполненное rate-window откладывает dispatch, но не отвергает bounded future queue;
- exact minute limit блокирует Telegram gateway и не увеличивает `attempt_count`;
- durable PREPARED reservations не позволяют двум workers занять один slot;
- финальная reservation ловит гонку, возникшую после Safety precheck;
- rejected reservation создаёт `abandoned` evidence без `network_started_at`;
- partial policy update повторно проверяет effective row;
- staged batch остаётся `held` при нехватке ready budget;
- manual retry остаётся атомарным под queue pressure;
- worker создаёт не более одного assessment за настроенный интервал;
- изменение policy делает прежний assessment stale, не переписывая evidence;
- Prometheus metrics, alerts и Grafana panels входят в release assets;
- tenant isolation и Viewer read-only соблюдаются;
- production runtime запрещает отключённый обязательный gate.
- capacity-настройки и новый Alembic head входят в runtime fingerprint active/standby площадок.

## Алгоритмические решения

### Admission

Admission выполняется до изменения очереди и повторяется под транзакцией scheduler. Это закрывает TOCTOU между пользовательским запросом и фактическим созданием jobs.

### Ready release

`held → pending` выполняется только после отдельной проверки ready budget. При отказе staged batch не изменяется.

### Dispatch

Safety Engine выполняет precheck до gateway и счётчика попыток. После создания durable `DeliveryAttempt(status=prepared)` выполняется финальная проверка под блокировкой CapacityPolicy. Конкурирующая reservation, превысившая budget, переводится в `abandoned`; Telegram gateway не создаётся.

### Exact limits

Предварительная проверка блокирует `count >= limit`. Финальная проверка учитывает собственную уже сохранённую reservation и блокирует только `projected count > limit`. Это допускает ровно один текущий slot и останавливает следующий конкурентный worker.

### Drain estimate

Прогноз учитывает connection interval, minute/hour budgets и ближайшее освобождение sliding window. Пустая очередь не получает фиктивное время ожидания только из-за ранее заполненного rate-window.

### Runtime fencing

Дополнительный аудит выявил, что runtime evidence 2.4 всё ещё ссылался на старый migration head и не включал Capacity Assurance. В 2.5 expected head обновлён до `bd5f7a9c3e6f`, а все site-level capacity defaults/gates включены в critical configuration SHA-256. Active и standby с разными capacity-настройками теперь несовместимы до failover.

## Release gates

```text
Python compileall:                         PASS
Ruff lint/format:                          PASS
Mypy (111 production files):               PASS
app.js syntax:                             PASS
service worker syntax:                     PASS
function documentation gate:               PASS
shell syntax:                              PASS
release asset validator:                   PASS
Compose YAML:                              PASS
Prometheus YAML:                           PASS
Grafana JSON:                              PASS
Nginx default config:                      PASS
Nginx HTTPS config:                        PASS
systemd unit validation:                   PASS
Alembic fresh upgrade:                     PASS
Alembic schema check:                      CLEAN
Downgrade 2.5 → 2.4:                       PASS
Upgrade 2.4 → 2.5:                         PASS
Повторный schema check:                    CLEAN
HTTP login/cookies/CSRF/dashboard/logout:  PASS
doctor:                                    PASS
audit hash-chain verification:             PASS
master-key rotation dry-run:               PASS
worker --once:                             PASS
signed recovery backup:                    PASS
backup verification:                       PASS
isolated SQLite restore drill:             PASS
PWA sensitive-cache tests:                 PASS
Live UI desktop/tablet/mobile:              PASS
```

Worker smoke:

```text
runs=0 delivery=0 inbound=0 outbox=0 privacy=0 slo=1 capacity=1
continuity_scanned=0 continuity_updated=0 continuity_errors=0
```

Recovery smoke:

```text
backup_status=verified
manifest_file_count=3
drill_status=passed
drill_mode=sqlite_isolated
rto_met=true
```

## Миграционный round-trip

```text
2.4 ac4e6f8b2d5f
→ upgrade
2.5 bd5f7a9c3e6f
→ downgrade
2.4 ac4e6f8b2d5f
→ upgrade
2.5 bd5f7a9c3e6f
```

После повторного upgrade:

```text
No new upgrade operations detected.
```

## Статические release-проверки

Проверены:

- version markers 2.5.0;
- обязательные development/production Compose variables;
- production Capacity Assurance gate;
- наличие migration, API, service, docs и tests 2.5;
- admission, ready-release, Safety Engine и durable reservation integrations;
- manual retry и periodic worker assessment;
- capacity metrics, alerts и Grafana panels;
- все 32 SPA-раздела;
- валидность JSON/YAML и локальных Markdown-ссылок;
- отсутствие секретов, БД, Telegram sessions, backup-артефактов, cache и Git metadata в release-наборе.

## Историческая проверка исходного архива 2.5

Ниже сохранены evidence исходного кандидата от 9 августа. Они не подменяют финальную
проверку текущего дерева после аудита; новый manifest генерируется
`scripts/generate_manifest.py`, а итоговый архив должен быть собран из нового Git commit.

```text
ZIP entries:                              282
MANIFEST.sha256 entries:                 281
ZIP CRC:                                  PASS
MANIFEST.sha256:                          281/281 PASS
Forbidden-file scan:                      PASS
Release asset validator:                  PASS
Функциональные тесты после распаковки:    298/298 PASS
Coverage processes после распаковки:      21/21 PASS
Statements после распаковки:              17 861
Missed statements после распаковки:       3 491
Statement coverage после распаковки:      80,45%
Migration round-trip:                     PASS
HTTP login/cookies/CSRF/dashboard/logout: PASS
worker --once:                            PASS
audit hash-chain verification:            PASS
master-key rotation dry-run:               PASS
signed recovery backup/verify/drill:      PASS
```

Окончательный архив пересобирается только после добавления этого QA-evidence и нового manifest. Изменения между полностью проверенным кандидатом и финальным архивом ограничены release-метаданными и контрольными суммами; финальный ZIP дополнительно проходит CRC, manifest, forbidden-file scan, статический validator, миграционный smoke, HTTP smoke, worker и recovery smoke.

## Что нельзя подтвердить без инфраструктуры владельца

Не заявляются как пройденные:

- production load-test PostgreSQL, Redis и нескольких workers;
- принятие фактических minute/hour throughput thresholds;
- реальная Bot API canary и MTProto-авторизация;
- публикация в служебную и пять разрешённых групп;
- Telegram Business webhook;
- реальный двуххостовый continuity/failover drill;
- PostgreSQL HA и network partition;
- production Docker image build — Docker CLI/daemon в текущей среде отсутствуют;
- внешний vulnerability scanner и актуальная advisory database.

Автоматические тесты не выявили известных дефектов в смоделированных сценариях, но абсолютную безошибочность нельзя доказать без live-инфраструктуры и реальной Telegram-приёмки.
