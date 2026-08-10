# TeleFlow Platform 1.7.0 — Artifact Trust & Signed Evidence

## Назначение релиза

Версия 1.7 закрывает разрыв между контролем целостности и подтверждением происхождения переносимых файлов. Configuration bundles, support bundles и pilot acceptance reports теперь могут подписываться Ed25519 и проверяться как внутри платформы, так и автономно.

## Добавлено

- tenant-scoped Ed25519 signing keys;
- зашифрованное хранение приватной части через AES-256-GCM;
- публичный PEM/base64 export без раскрытия приватного ключа;
- локальный trust store и импорт доверенных публичных ключей;
- статусы `unsigned`, `valid_untrusted`, `valid_trusted`, `invalid`, `revoked`;
- основной signing key организации;
- необратимый revoke с уничтожением зашифрованной приватной части;
- подпись configuration bundle manifest;
- подпись support bundle manifest;
- подпись pilot acceptance payload;
- строгая проверка signature purpose и подписанных метаданных;
- автономный CLI `scripts/verify_artifact.py`;
- API и 24-й раздел SPA «Подписи и доверие»;
- commissioning-проверка signing policy и default key;
- ротация приватных signing keys вместе с master key;
- signature metadata в БД, API и download headers;
- production policy `require_trusted`.

## Security hardening

- подпись доменно разделена строкой `TeleFlow-Artifact-Signature-v1`;
- все метаданные signature envelope входят в подписываемое сообщение;
- разные artifact types используют разные `purpose`;
- ZIP verifier запрещает path traversal, symlink, encrypted entries, duplicates и незаявленные файлы;
- pilot acceptance manifest имеет строгую схему и сверяет алгоритм/время;
- API distinguish known, trusted and revoked signers;
- default-key updates сериализуются блокировкой organization row;
- private signing material никогда не возвращается через schemas/API/UI;
- production не запускается без `require_trusted`;
- release validator проверяет наличие signing-кода, migration, документации и отсутствие `private_key_enc` во frontend.

## Совместимость

- при policy `optional` legacy bundles без подписи можно просмотреть и импортировать;
- недействительная или отозванная подпись блокируется при любой policy;
- `require_valid` принимает корректную подпись без локального trust record;
- `require_trusted` требует активный доверенный ключ;
- импортированные configuration bundles по-прежнему создают fail-safe выключенные/draft сущности.

## База данных

Новый Alembic head:

```text
3b7d9f1a2c4e
```

Предыдущий head:

```text
2a6f0104062a
```

Миграция добавляет:

- `artifact_signing_keys`;
- signature status/info/fingerprint для `configuration_bundles`;
- signature status/info/fingerprint для `support_bundles`.

## Обновление

1. Остановить API и worker.
2. Создать и проверить backup.
3. Обновить код и зависимости.
4. Выполнить `alembic upgrade head`.
5. Сгенерировать локальный Ed25519-ключ.
6. Экспортировать публичный PEM и сохранить fingerprint.
7. В production установить `TELEFLOW_ARTIFACT_SIGNATURE_POLICY=require_trusted`.
8. Выполнить commissioning и автономную проверку тестового bundle.
9. Запустить сервисы.

## Acceptance

Релиз считается принятым после:

- успешного fresh migration и round-trip 1.6↔1.7;
- генерации ключа без утечки приватной части;
- проверки signed configuration/support/acceptance artifacts;
- обнаружения изменения payload, manifest и signature metadata;
- блокировки revoked и untrusted signer по production policy;
- проверки cross-tenant explicit trust;
- master-key rotation test;
- полного release-QA из распакованного ZIP.

Финальный release run: 166 тестов и 12 408 statements. Консервативный зафиксированный результат — 2 711 missed и 78,15% coverage; повторный прогон непосредственно после распаковки итогового ZIP — 2 709 missed и 78,17%. Gate — 65%. Подробности находятся в `QA_REPORT.md` и `BUILD_INFO.json`.
