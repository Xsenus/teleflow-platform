# Release notes

## Unreleased — audit hardening 2026-08-10

- Сквозной Windows/Linux-ready QA, строгая типизация и русскоязычная документация функций.
- Исправления recovery SQLite lifecycle и доступности динамических форм.
- Responsive browser acceptance для всех 32 разделов.
- 374 теста, 85,51% statement coverage и gate 80%; оптимизированные изолированные фикстуры.
- Полные API lifecycle-тесты диалогов, кандидатов, автоматизации, интеграций и шаблонов.
- Изолированные contract-тесты Telegram Bot API, OpenAI-compatible adapter, media validation и Redis-locks.
- Полная mock-проверка MTProto/2FA, worker orchestration, Business client и External API.
- GitHub CI/CodeQL/Dependabot, шаблоны сопровождения и проверяемый source manifest.

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


## 1.9.0 — 2026-08-07

- Change requests with independent Owner/Admin approval.
- Planned maintenance mode blocks publisher scheduler, manual launch/resume and pre-network Safety Engine.
- Persisted pre/post deployment verification with Alembic, application-version, worker and audit-chain checks.
- Controlled complete/fail/cancel lifecycle and rollback evidence.
- New migration head `5d9f1b3c7e2a` and 26th web section «Изменения».

## 1.8.0 — 2026-08-07

Recovery Assurance & Continuity release.

### Recovery evidence

- tenant-scoped RPO/RTO policy and retention requirements;
- signed backup receipt and signed archive manifest;
- explicit registered/verified lifecycle;
- full archive/file hash verification before RPO compliance;
- signed restore-drill evidence.

### Safe restoration

- real isolated SQLite restore drill;
- PostgreSQL metadata-only validation without a false full-restore claim;
- non-destructive `restore.sh` wrapper;
- metadata-only web UI/API: raw dumps and age identities remain outside browser sessions.

### Security

- production requires trusted Ed25519 evidence and age encryption;
- strict ZIP composition/path/symlink/duplicate checks;
- cross-tenant, future timestamp and tampered evidence rejection;
- plaintext cleanup after failed encryption;
- commissioning blocks on recovery policy violations.

### QA

- 183 automated tests across ten isolated coverage shards;
- 13,373 statements, conservative 2,886 missed, 78.42% statement coverage; observed range 78.42–78.43%; gate 65%;
- clean Alembic head `4c8e0a2b6d1f` and 1.7↔1.8 roundtrip;
- HTTP, worker, audit, key-rotation and backup→verify→isolated-drill gates.

Detailed notes: `docs/RELEASE_NOTES_1.8.md`.

## 1.7.0 — 2026-08-06

Artifact Trust & Signed Evidence release.

### Artifact provenance

- Ed25519 keys scoped to each organization;
- signed configuration/support ZIP manifests and pilot acceptance payloads;
- explicit trusted/untrusted/revoked signer states;
- public PEM export and cross-installation trust ceremony;
- autonomous verifier CLI.

### Security

- private keys encrypted by the platform master key and removed on revoke;
- signature metadata and purpose are cryptographically bound;
- production policy requires a trusted active signer;
- strict ZIP, manifest and acceptance wrapper verification;
- signing key participates in commissioning, audit and master-key rotation.

### QA

- 166 automated tests across nine isolated coverage shards;
- 12,408 statements; conservative 2,711 missed / 78.15% coverage, with 2,709 missed / 78.17% after unpacking the final ZIP; gate 65%;
- clean Alembic head `3b7d9f1a2c4e` and 1.6↔1.7 roundtrip;
- HTTP, worker, audit, key-rotation, infrastructure and offline-verifier gates.

Detailed notes: `docs/RELEASE_NOTES_1.7.md`.

## 1.6.0 — 2026-08-06

Commissioning & Portability release.

### Commissioning

- persisted infrastructure diagnostics with TTL and SHA-256 fingerprint;
- database, migrations, storage, lock backend, worker, audit-chain, TOTP, HTTPS, backup and antivirus checks;
- commissioning result is required by formal pilot stages.

### Formal pilot program

- sequential local/live stages for 1, 5, 20, 50 and 100 destinations;
- immutable campaign-run evidence and SHA-256 verification;
- distinct Owner/Admin sign-off for live stages;
- acceptance report with payload checksum.

### Safe portability

- ZIP export/import of non-secret reference configuration;
- credentials, Telegram sessions, approvals and active permissions are excluded;
- imported connections/destinations/campaigns/automations are fail-safe disabled or draft;
- strict ZIP/manifest/schema/media validation;
- tenant-scoped no-store download and audited storage deletion.

### QA

- 155 automated tests across eight isolated coverage shards;
- 11,686 statements, 2,575 missed, statement coverage 77.97%, gate 65%;
- 1.5↔1.6 migration roundtrip;
- HTTP, worker, audit, key-rotation and infrastructure gates.

Detailed notes: `docs/RELEASE_NOTES_1.6.md`.

## 1.5.0 — 2026-08-06

Pilot Certification & Diagnostics release.

### Controlled scale

- sequential stage limits `LOCAL`, `SERVICE`, `FIVE`, `TWENTY`, `FIFTY`, `HUNDRED`;
- enforcement in preview, approval, run-now, resume, scheduler and Safety Engine;
- fixed one-shot live canary with no arbitrary text or automatic retry;
- persisted assessment with TTL and fingerprint;
- real previous-stage delivery evidence; fake jobs do not count;
- safe stage lowering and automatic campaign pause.

### Diagnostics

- temporary redacted support bundle;
- no Telegram credentials, PII or message bodies;
- pseudonymized internal IDs, SHA-256 and internal manifest;
- no-store download and retention cleanup.

### QA

- 134 automated tests across seven isolated coverage shards;
- 10,316 statements, 2,309 missed, statement coverage 77.62%, gate 65%;
- 1.4↔1.5 migration roundtrip;
- HTTP, worker, audit, key-rotation and release-asset gates.

Detailed notes: `docs/RELEASE_NOTES_1.5.md`.

## 1.4.0 — 2026-08-06

Production Pilot release: persisted readiness, destination validation history, write-forbidden lifecycle and organization/connection/destination blackout calendar.

Detailed notes: `docs/RELEASE_NOTES_1.4.md`.

## 1.3.0 — 2026-08-06

Controlled Operations release TeleFlow Platform.

### Preflight and permission lifecycle

- persisted tenant-scoped preflight reports with TTL;
- a fresh scheduler preflight immediately before every run;
- permission review/expiry fields and delivery-time expiry enforcement;
- per-destination content fingerprint and recent duplicate guard.

### Staged rollout

- standard or staged campaign mode;
- first batch `pending`, later batches physically `held`;
- manual checkpoint, optional threshold-based automatic release and controlled abort;
- held jobs are not selected by worker/retry/resume.

### Ambiguous delivery reconciliation

- explicit `confirmed_sent`, `confirmed_not_sent` or `skipped`;
- no automatic retry after unknown Telegram network result;
- campaign cancel/staged abort cannot erase unresolved evidence;
- connection revoke destroys credentials but preserves uncertain jobs for review.

### QA

- 107 automated tests;
- 8862 statements, 2136 missed, statement coverage 75.90%, gate 65%;
- 1.2↔1.3 migration roundtrip;
- HTTP, worker, audit, key-rotation and release-asset gates.

Detailed notes: `docs/RELEASE_NOTES_1.3.md`.

## 1.2.0 — 2026-08-06

Governance & Resilience release TeleFlow Platform.

### Controlled approvals

- explicit preview → approval request → decisions → run workflow;
- optional four-eyes separation;
- configurable multi-approval for high-risk destination sets;
- expiring requests and proactive worker expiry;
- SHA-256 fingerprint invalidated by content, route, schedule or permission changes.

### Operational resilience

- organization-wide emergency stop before any Telegram network call;
- held jobs move to `waiting_review` and require explicit resume;
- tenant-scoped notification center with deduplication and acknowledgement;
- critical notifications for Telegram restrictions, authorization loss and ambiguous delivery.

### Audit and secrets

- sequential tamper-evident audit hash-chain;
- offline legacy audit backfill and verification CLI;
- master-key preflight, dry-run and controlled rotation across encrypted fields;
- Alembic migration `f4a6b8c12d34`.

### QA

- 96 automated tests;
- statement coverage 74.15%, gate 65%;
- 1.1↔1.2 migration roundtrip;
- HTTP, worker, audit and key-rotation release gates.

Detailed notes: `docs/RELEASE_NOTES_1.2.md`.

## 1.1.0 — 2026-08-06

Production enhancement release TeleFlow Platform.

### Publisher operations

- safe destination TXT/CSV/TSV preview/apply/export;
- discovery dialogs already available to Bot API/MTProto connection;
- no auto-join and no automatic permission confirmation;
- per-destination timezone, weekdays, time window and cooldown;
- campaign edit and copy workflow;
- stable A/B template assignment with exact delivery snapshots.

### Business automation

- versioned deterministic candidate flows;
- visual flow editor and drag-and-drop order;
- message/question/choice/handoff/end nodes;
- typed candidate field validation;
- encrypted phone/email handling.

### Analytics and security

- delivery/dialog/candidate/AI/outbox analytics;
- candidate funnel and A/B delivery breakdown;
- anonymized CSV export;
- active browser sessions and selective revoke;
- optional fail-safe ClamAV media scan;
- PWA shell that never caches API/auth/PII.

### QA and release engineering

- 81 automated tests;
- statement coverage above 72%, gate 65%;
- service-worker syntax and cache-policy tests;
- migrations for flows, destination windows and template variants;
- deterministic test timeout and CI stdout handling.

Detailed notes: `docs/RELEASE_NOTES_1.1.md`.

### External acceptance required

Live Bot API, MTProto, Telegram Business, external AI, Google Sheets, S3, ClamAV and production TLS require owner credentials/infrastructure and are accepted through `docs/PILOT_CHECKLIST.md`.

## 1.0.0 — 2026-08-05

Core roadmap release:

- multi-tenant organizations;
- Bot API/MTProto publisher;
- Telegram Business inbox;
- candidates and AI;
- integrations/privacy;
- observability, deployment and backup;
- 43 automated tests.

## 0.1.0 — 2026-08-05

First safe publisher MVP:

- web panel/FastAPI;
- Bot API/MTProto;
- destinations/templates/campaigns/queue;
- security baseline;
- fake transport;
- 27 tests.
