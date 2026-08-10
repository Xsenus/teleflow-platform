# Release Trust — TeleFlow Platform 2.0

## Назначение

Release Trust связывает change-management с конкретной проверяемой сборкой TeleFlow. Для production upgrade недостаточно указать строку версии: администратор должен привязать доверенный release attestation, подписанный активным Ed25519-ключом организации.

## Что входит в provenance

Payload содержит только метаданные релиза:

- продукт и версия;
- source commit, когда он присутствует в `BUILD_INFO.json`;
- SHA-256 `BUILD_INFO.json`;
- SHA-256 `MANIFEST.sha256`;
- SHA-256 `requirements.txt` и `requirements-dev.txt`;
- нормализованный список runtime/dev зависимостей;
- количество runtime-зависимостей.

Токены Telegram, API-ключи, приватные ключи, `.env`, пользовательские данные и содержимое базы в attestation не включаются.

## Подпись

Канонический JSON подписывается Ed25519 с отдельным purpose:

```text
teleflow-release-attestation-v1
```

Проверка использует существующий trust store организации. Статусы подписи: `unsigned`, `valid_untrusted`, `valid_trusted`, `invalid`, `revoked`.

## Production gate

В production обязательно:

```text
TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION=true
TELEFLOW_ARTIFACT_SIGNATURE_POLICY=require_trusted
```

Для `upgrade` change request pre-change verification проверяет:

1. наличие `release_attestation_id`;
2. принадлежность attestation той же организации;
3. точное совпадение `target_version`;
4. неизменность canonical payload SHA-256;
5. действительность Ed25519-подписи;
6. доверие активному ключу и отсутствие отзыва.

Без этого изменение остаётся заблокированным до любых операций обновления.

## Веб-панель

Раздел **Доверие к релизам** позволяет:

- подписать текущую установленную сборку;
- увидеть версию, commit, payload SHA-256 и количество зависимостей;
- повторно проверить доверие;
- выбрать attestation при создании upgrade change request.

## Перенос между инсталляциями

Для новой сборки можно передать JSON payload и signature envelope через API `/release-attestations/import`. Сначала публичный ключ источника необходимо добавить в раздел «Подписи и доверие» и сверить fingerprint по независимому каналу.

## Ограничения

Release Trust подтверждает происхождение и неизменность заявленного provenance. Он не является онлайн-сканером CVE и не заменяет CI dependency scanning, reproducible builds или внешний transparency log. Эти проверки можно добавлять в CI поверх attestation.
