# Changelog

## Unreleased — audit hardening 2026-08-10

- Исправлены UTC-зависимый analytics test и утечки SQLite-соединений recovery-проверки на Windows.
- Устранены ошибки строгой типизации; Ruff и mypy добавлены в обязательный CI.
- Динамические формы получили программные связи label/field, включая повторный render flow builder.
- Выполнена живая проверка 32 SPA-разделов на desktop, tablet и mobile.
- Все 1 477 Python- и 156 JavaScript-функций защищены русскоязычным documentation gate.
- Тестовые БД клонируются из эталона; полный Windows-прогон ускорен примерно на 62%.
- Coverage gate повышен с 65% до 80%.
- Добавлены GitHub issue/PR templates, CodeQL, Windows CI, SHA-pinned Actions и Dependabot.
- Добавлен детерминированный генератор и verifier `MANIFEST.sha256`.
- Duplicate provider теперь возвращает управляемый HTTP 409 даже при гонке на database flush.
- Добавлены lifecycle- и adapter-тесты; coverage production-кода повышен до 83,22%.

## 2.5.0 — 2026-08-09

- Capacity & Backpressure Assurance.
- Tenant-scoped ordered limits for active, ready and processing jobs, active runs and run size.
- Immutable assessments with policy SHA-256, TTL, blockers, warnings and estimated drain time.
- Admission control before run/job creation and a transactional scheduler recheck.
- Ready-budget for staged rollout, checkpoints and manual retry.
- Minute/hour network-start budgets and durable PREPARED dispatch reservations.
- Safety Engine blocks exact limits and concurrency races before Telegram gateway creation.
- Periodic worker assessments, Prometheus/Grafana and the 32nd SPA section «Нагрузка и лимиты».
- Production requires `TELEFLOW_CAPACITY_ASSURANCE_REQUIRED=true`.
- Alembic head `bd5f7a9c3e6f`.

## 2.4.0 — 2026-08-09

- Continuity simulation без Telegram-сети и изменения execution lease.
- Live `Primary → Standby → Primary` drill через production fencing.
- Runtime compatibility evidence и SHA-256 event chain.
- Независимый sign-off, RTO и срок действия evidence.
- Worker synchronization с savepoint isolation.
- Commissioning/Prometheus/Grafana continuity gates.
- Docstring/JSDoc для всех именованных функций и автоматический documentation gate.
- 31-й раздел панели «Непрерывность».
- Alembic head `ac4e6f8b2d5f`.

## 2.3.0 — 2026-08-08

- Execution Fencing & Failover Assurance.
- Tenant-scoped active/standby registry, worker heartbeat and monotonic epoch lease.
- Durable delivery-attempt ledger distinguishes pre-network crash from ambiguous post-network crash.
- Controlled failover uses draining, blocker snapshots and independent Owner/Admin approval.
- Scheduler and pre-network Safety Engine reject standby/stale workers.
- Prometheus execution metrics/alerts and 30th SPA section «Active / Standby».
- Alembic head `9b3d5f7a1c4e`.


## 2.2.0 — 2026-08-07

- Operational SLO & Incident Assurance.
- Tenant-scoped SLO policy, immutable assessments, delivery success and error budget.
- Publishing/change gates are enforced by API, scheduler and pre-network Safety Engine.
- Managed incident lifecycle with automatic deduplicated SLO incidents and event history.
- Prometheus/Grafana operational metrics and production-required SLO gate.
- Alembic head `8a2c4e6f0b3d`.


## 2.1.0 — 2026-08-07

- Release Transparency & Dependency Assurance.
- Signed tenant-scoped release transparency hash-chain with publish/withdraw lifecycle.
- Deterministic CycloneDX 1.5 SBOM and immutable dependency assessments.
- Upgrade change requests bind to an exact assessment and current dependency policy.
- Production gates require transparency, vulnerability scan and trusted report.
- Alembic head `7f1b3d5e9a2c`.


## 2.0.0 — 2026-08-07

- Release Trust & Provenance.
- Signed Ed25519 release attestations and dependency inventory.
- Production upgrade gate binds change requests to a trusted target build.
- Alembic head `6e0a2c4f8b1d`.


Все значимые изменения документируются в `RELEASE_NOTES.md` и versioned release notes в `docs/`.

## 1.9.0 — 2026-08-07

- Change requests with independent Owner/Admin approval.
- Planned maintenance mode blocks publisher scheduler, manual launch/resume and pre-network Safety Engine.
- Persisted pre/post deployment verification with Alembic, application-version, worker and audit-chain checks.
- Controlled complete/fail/cancel lifecycle and rollback evidence.
- New migration head `5d9f1b3c7e2a` and 26th web section «Изменения».

## 1.8.0 — 2026-08-07

### Added

- tenant-scoped Recovery Assurance policy with RPO, RTO, drill age and retained-copy requirements;
- signed backup receipts and signed `MANIFEST.json` with archive/file SHA-256;
- explicit `registered → verified` backup evidence lifecycle;
- isolated SQLite restore drill and signed drill receipts;
- metadata-only PostgreSQL check that is not treated as full restore;
- 25th SPA section “Восстановление” and metadata-only Recovery API;
- safe backup, verification and restore-drill CLI commands;
- commissioning recovery gate and retention expiry lifecycle.

### Security

- production requires age encryption, trusted signatures and configured age recipient;
- raw backup files and age identities never pass through the web API;
- ZIP traversal, absolute paths, symlinks, duplicates and undeclared files are rejected;
- failed encryption removes plaintext/partial output;
- successful drill evidence requires a physically verified archive;
- legacy `restore.sh` is now a non-destructive isolated-drill wrapper.

### Database

- Alembic head `4c8e0a2b6d1f`; downgrade target `3b7d9f1a2c4e`.

### QA

- 183 tests across 10 isolated coverage shards;
- 13,373 statements, conservative 2,886 missed, 78.42% statement coverage;
- migration, HTTP, worker, audit, key-rotation and recovery smoke gates.

## 1.7.0 — 2026-08-06

### Added

- tenant-scoped Ed25519 signing keys and explicit public-key trust store;
- signed configuration bundles, support bundles and pilot acceptance reports;
- autonomous ZIP/JSON verifier CLI and artifact inspection API;
- 24th SPA section “Подписи и доверие”;
- commissioning and master-key rotation support for signing keys.

### Security

- private signing material is AES-256-GCM encrypted and never returned through API/UI;
- domain-separated signatures bind purpose, key metadata, digest and timestamp;
- production requires an active explicitly trusted default signer;
- revoke destroys the encrypted private part while retaining public verification metadata;
- strict acceptance manifest and archive composition validation.

### Database

- Alembic head `3b7d9f1a2c4e`; downgrade target `2a6f0104062a`.

## 1.6.0 — 2026-08-06

### Added

- persisted commissioning checks for DB, migrations, storage, worker, locks, audit, TOTP, HTTPS, backups and antivirus policy;
- formal pilot programs with local fake stage and certified live sizes 1/5/20/50/100;
- immutable evidence snapshots, SHA-256 integrity and independent live-stage sign-off;
- acceptance report for completed pilot programs;
- safe configuration ZIP export, preview and import without credentials or active permissions;
- fail-safe imported state for connections, destinations, campaigns, flows, providers, policies, integrations and blackouts;
- audited configuration-bundle deletion with storage cleanup;
- 23rd SPA section “Ввод в эксплуатацию”.

### Security

- strict ZIP path, duplicate, symlink, encryption, manifest, schema and secret-like field validation;
- imported Telegram connections contain no credentials and cannot send until reauthorized;
- imported destinations are disabled, unverified and require fresh permission evidence;
- configuration downloads use `Cache-Control: no-store`;
- live pilot stages require current commissioning, approval, readiness and exact route size.

### Database

- Alembic head `2a6f0104062a`; downgrade target `e2f4a6c8d0b1`.

## 1.5.0 — 2026-08-06

### Added

- Pilot stage `LOCAL → SERVICE → FIVE → TWENTY → FIFTY → HUNDRED` with server-side route limits.
- One-shot fixed-body Telegram canary with no automatic retry.
- Persisted stage assessments with TTL, checks, blockers and state fingerprint.
- Real-delivery evidence requirements; fake delivery does not count.
- Safe stage lowering that pauses oversized campaigns and holds queued jobs.
- Redacted temporary support bundle with SHA-256 and internal manifest.
- Pilot certification UI, API, migration, tests and production settings.

### Security

- Row locks serialize concurrent canary checks per destination and connection.
- Stage enforcement runs in preview, approval, run-now, resume, scheduler and Safety Engine.
- Support bundles exclude Telegram credentials, PII, message bodies and raw audit details.
- Production startup requires pilot stage enforcement.
- Canary respects the organization emergency stop before gateway creation.
- Stage fingerprints include worker freshness and unacknowledged critical events.
- Ready destinations are paired with their own healthy Telegram connection.

### Database

- Alembic head `e2f4a6c8d0b1`; downgrade target `c9e1f3a5b7d9`.

## 1.4.0 — 2026-08-06

- добавлен Production Pilot readiness с TTL, campaign fingerprint и structured checks;
- введена история технической проверки destinations и пакетная revalidation до 50 групп;
- `CHAT_WRITE_FORBIDDEN` классифицируется отдельно, отключает destination и сохраняется в history;
- добавлен операционный blackout calendar уровня organization/connection/destination;
- weekly blackout поддерживает IANA timezone и окна через полночь;
- `run-now`, `resume` и scheduler могут требовать актуальный readiness report;
- stale readiness ставит due-кампанию на паузу и создаёт critical audit/notification;
- в Web UI добавлен 22-й раздел Production Pilot;
- исправлен двойной increment delivery attempt counter и сохранение custom destination title;
- Alembic head обновлён до `c9e1f3a5b7d9`;
- release-набор расширен до 121 теста; statement coverage — 77,04%.

## 1.3.0 — 2026-08-06

- добавлены persisted preflight reports с TTL и обязательная свежая проверка непосредственно перед run;
- введён lifecycle срока действия разрешений групп с предупреждением и блокировкой после истечения;
- добавлены destination-scoped content fingerprints и duplicate guard без изменения текста;
- реализован staged rollout: `pending` первый пакет, физически `held` последующие, checkpoint, threshold и controlled abort;
- добавлена ручная reconciliation `confirmed_sent / confirmed_not_sent / skipped` для неоднозначной доставки;
- campaign cancel/staged abort не могут уничтожить uncertain result, а connection revoke сохраняет его для сверки;
- добавлены UI/API для preflight, run batches, checkpoint, abort и delivery review;
- Alembic head обновлён до `b7c9d2e4f6a8`;
- release-QA расширен до 107 tests, coverage 75,90%, добавлен static release-asset validator.

## 1.2.0 — 2026-08-06

- введён явный workflow campaign approval без неявного разрешения через run-now;
- добавлены four-eyes, multi-approval для high-risk routes и TTL запросов;
- утверждение связано с SHA-256 fingerprint текста, маршрута, расписания и permission evidence;
- добавлен organization-wide emergency publishing stop до Telegram network call;
- добавлен tenant-scoped notification center с read/ack и outbox events;
- audit log связан tamper-evident SHA-256 hash-chain и получил verify/backfill CLI;
- добавлена preflight/dry-run ротация master key для encrypted DB/storage fields;
- Alembic head обновлён до `f4a6b8c12d34`;
- release-QA расширен до 96 tests, coverage 74,15%.

## 1.1.0 — 2026-08-06

- добавлены tenant-scoped analytics, candidate funnel, daily timeseries и обезличенный CSV;
- добавлены browser session list, selective revoke и revoke-others;
- реализованы versioned visual automation flows с graph validation и drag-and-drop;
- реализованы safe TXT/CSV/TSV destination import/export и discovery уже доступных dialogs;
- добавлены per-destination timezone/weekdays/time window/cooldown;
- добавлена опциональная ClamAV INSTREAM-проверка media до storage;
- добавлены campaign editing/copy и стабильные A/B template variants;
- добавлен privacy-safe installable PWA shell;
- release-QA расширен до 81 tests, coverage выросло выше 72%;
- улучшена воспроизводимость CI/test shutdown и миграционная проверка.

## 1.0.0 — 2026-08-05

- завершены multi-tenant, Business inbox, AI, candidates, integrations, privacy, observability и production operations;
- исправлены tenant-scoped caps, webhook auth ordering, production schema policy, worker readiness time handling, outbox partial retry semantics, Redis healthcheck и reverse-proxy Host forwarding;
- тестовый набор расширен с 27 до 43 scenarios.

## 0.1.0 — 2026-08-05

- первый безопасный Telegram publisher MVP.
