# TeleFlow Platform 2.1.0 — Release Transparency & Dependency Assurance

Дата: 7 августа 2026 года.

## Основное

- добавлен tenant-scoped подписанный release transparency log;
- каждая запись защищена hash-chain и отдельной Ed25519-подписью;
- поддержаны события публикации и отзыва release attestation;
- добавлен детерминированный CycloneDX 1.6 SBOM;
- реализован scanner-neutral dependency report и импорт vulnerability findings;
- добавлены dependency policies: pins, prerelease, severity thresholds, denied packages, TTL и trusted report;
- report, SBOM, policy snapshot и release attestation закрепляются неизменяемыми SHA-256 evidence;
- change request привязывается к конкретному dependency assessment ID;
- pre-change verification не выбирает более поздний assessment автоматически;
- изменение policy, SBOM, report, attestation или signature блокирует старое evidence;
- server-side TTL является жёстким верхним пределом для policy;
- добавлен 28-й раздел веб-панели «Поставка и зависимости»;
- добавлены API публикации/отзыва/проверки transparency chain, policy, assessments и SBOM;
- production требует transparency, vulnerability scan и trusted dependency report;
- новая миграция Alembic `7f1b3d5e9a2c`;
- round-trip target: 2.0 head `6e0a2c4f8b1d`.
- full regression: 213 tests across 17 isolated coverage processes;
- statement coverage: 79.21%;
- release-QA separates full fixture teardown from deterministic coverage collection and exits only after each authoritative result is flushed, preventing telemetry helper threads from hanging CI.

## Security

- cross-tenant evidence не принимается;
- withdrawal требует причины;
- для одной версии нельзя оставить активными два разных attestation;
- oversized reports и более 5 000 findings блокируются до persistence;
- report обязан совпадать с серверным SBOM и release attestation;
- повторная verification не переписывает исходные evidence hashes;
- production startup отклоняет отключённые supply-chain gates;
- API mutation защищены auth, CSRF, RBAC, tenant scope и audit-chain.

## Operational note

Inventory-only assessment подтверждает состав заявленных зависимостей, но не считается vulnerability scan. Для production необходимо получить report от реального CI/scanner и связать его с SHA-256 серверного SBOM.
