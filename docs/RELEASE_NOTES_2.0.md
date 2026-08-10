# TeleFlow Platform 2.0.0 — Release Trust & Provenance

Дата: 7 августа 2026 года.

## Основное

- добавлена сущность `ReleaseAttestation`;
- локальная сборка получает подписанный Ed25519 provenance;
- поддержан импорт attestation от доверенной инсталляции;
- canonical payload защищён SHA-256;
- release attestation привязывается к `ChangeRequest`;
- production upgrade блокируется без trusted attestation целевой версии;
- pre-change verification показывает отдельную проверку `release_attestation`;
- добавлен раздел веб-панели «Доверие к релизам»;
- production Compose и env требуют `TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION=true`;
- новая миграция Alembic `6e0a2c4f8b1d`;
- добавлены tamper, tenant, signature и upgrade-gate тесты.

## Безопасность

Attestation не содержит secrets или пользовательские данные. Подпись использует отдельный domain/purpose и существующий реестр Ed25519-ключей организации. Отозванный или недоверенный ключ не удовлетворяет production-gate.
