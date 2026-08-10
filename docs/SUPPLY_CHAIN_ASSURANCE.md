# Supply Chain Assurance — TeleFlow Platform 2.1

## Назначение

Версия 2.1 расширяет Release Trust 2.0 двумя независимыми доказательствами перед обновлением платформы:

1. целевая сборка должна быть опубликована в локальном подписанном release transparency log;
2. change request должен быть привязан к конкретному актуальному dependency assessment этой же сборки.

Модуль не выполняет скрытую сетевую проверку и не объявляет inventory-only отчёт vulnerability scan. В production результат сканера формируется во внешнем доверенном CI/сканере, импортируется как нормализованный JSON и подписывается доверенным Ed25519-ключом организации либо поставляется с уже проверяемой подписью.

## Release transparency log

Каждое событие `published` или `withdrawn` содержит:

- монотонный `sequence` внутри организации;
- ID и SHA-256 release attestation;
- версию и source commit;
- SHA-256 предыдущей записи;
- время, пользователя и причину;
- SHA-256 текущей записи;
- отдельную Ed25519-подпись с purpose `teleflow-release-transparency-entry-v1`.

Организация блокируется на время добавления записи, поэтому два параллельных запроса не получают одинаковый sequence. Для одной версии нельзя одновременно оставить опубликованными два разных attestation. Отзыв требует явной причины.

Проверка цепочки обнаруживает:

- пропуск или перестановку sequence;
- изменение payload;
- разрыв `previous_hash`;
- изменение `entry_hash`;
- недействительную, недоверенную или отозванную подпись;
- изменение release attestation после публикации;
- несовпадение версии и source commit.

Это tenant-local transparency log. Он не является публичным внешним журналом или trusted timestamp authority; экспорт/репликация журнала во внешнее immutable-хранилище остаются инфраструктурной мерой.

## CycloneDX SBOM

Для release attestation сервер формирует детерминированный CycloneDX 1.6 SBOM на основании заявленных прямых runtime-зависимостей:

- нормализованное имя пакета;
- точная версия, когда зависимость закреплена `==`/`===`;
- исходная requirement-строка;
- PURL для точно закреплённых версий;
- SHA-256 связанного release attestation.

SBOM отражает declared direct dependencies. Транзитивные зависимости должен предоставить внешний scanner; они помечаются отдельным предупреждением, а не молча считаются прямыми.

## Dependency report

Нормализованный отчёт версии 1 содержит:

```json
{
  "schema_version": 1,
  "kind": "vulnerability_scan",
  "release_payload_sha256": "…",
  "sbom_sha256": "…",
  "generated_at": "2026-08-07T06:00:00+00:00",
  "scanner": {"name": "scanner", "version": "1.0"},
  "findings": []
}
```

Сервер отклоняет:

- неизвестные top-level поля;
- другой release attestation или SBOM;
- будущий timestamp;
- неизвестный severity;
- более 5 000 findings;
- oversized JSON;
- некорректные подписи;
- попытку одновременно импортировать подпись и повторно подписать отчёт локально.

Поддерживаемые severity: `critical`, `high`, `medium`, `low`, `unknown`.

## Неизменяемое evidence

При создании assessment фиксируются:

- нормализованный report и его SHA-256;
- signature envelope и fingerprint;
- CycloneDX SBOM и его SHA-256;
- snapshot dependency policy и его SHA-256;
- SHA-256 release attestation;
- агрегаты findings;
- blockers/warnings/status;
- срок действия.

Повторная проверка может обновить только производные статусы и результат текущей проверки подписи. Она не заменяет сохранённые report, SBOM, policy snapshot или их hashes. Любая подмена переводит evidence в `blocked`.

## Dependency policy

На организацию хранится одна policy:

- требовать точные pins;
- разрешать/запрещать prerelease;
- требовать настоящий vulnerability scan;
- требовать trusted signature;
- максимальное число critical/high/medium findings;
- denied packages;
- TTL отчёта.

Серверный `TELEFLOW_DEPENDENCY_ASSESSMENT_TTL_HOURS` является жёстким верхним пределом. Веб-настройка не может увеличить его. Изменение policy делает существующие assessments устаревшими и требует создать новое evidence.

## Привязка к change request

Upgrade change request хранит одновременно:

- `release_attestation_id`;
- `release_dependency_assessment_id`.

Pre-change verification не выбирает «самый свежий» assessment автоматически. Она проверяет именно закреплённый ID и требует:

1. совпадение организации;
2. совпадение release attestation;
3. неизменность report/SBOM/policy hashes;
4. актуальную текущую policy;
5. неистёкший TTL;
6. trusted signature, когда это требуется;
7. vulnerability scan, когда это требуется;
8. отсутствие blockers;
9. опубликованный attestation и целую transparency chain.

Такой контракт исключает подмену согласованного evidence более поздним отчётом после approval.

## Production configuration

В production обязательны:

```text
TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION=true
TELEFLOW_REQUIRE_RELEASE_TRANSPARENCY=true
TELEFLOW_REQUIRE_DEPENDENCY_ASSESSMENT=true
TELEFLOW_DEPENDENCY_REQUIRE_VULNERABILITY_SCAN=true
TELEFLOW_DEPENDENCY_REQUIRE_TRUSTED_REPORT=true
TELEFLOW_DEPENDENCY_ASSESSMENT_TTL_HOURS=24
TELEFLOW_DEPENDENCY_REPORT_MAX_BYTES=5242880
```

Startup отклоняет конфигурацию, в которой обязательные gates отключены.

## Рекомендуемый процесс обновления

```text
CI/build
  → release attestation
  → CycloneDX SBOM
  → внешний vulnerability scan
  → нормализованный и подписанный report
  → dependency assessment
  → publish в transparency log
  → change request с точными evidence IDs
  → independent approval
  → pre-change verification
  → maintenance / deployment
  → post-change verification
```

## Ограничения и остаточные риски

- локальный журнал не доказывает глобальную публичность события;
- SBOM строится из declared requirements и не подтверждает фактически установленное окружение без CI scanner;
- доверенная подпись подтверждает источник report, но не качество vulnerability database;
- новый advisory после создания assessment обнаружится только новым scan;
- компрометация default signing key требует revoke, incident response и нового evidence;
- для высокого уровня assurance рекомендуется CI-generated SBOM/scan, pinned lockfile, external immutable archive и независимая сверка fingerprints.
