# TeleFlow Platform 1.6.0 — Commissioning & Portability

Дата релиза: **6 августа 2026 года**

Версия 1.6 добавляет управляемый ввод системы в эксплуатацию и безопасный перенос справочной конфигурации между локальным ПК, staging и production-сервером. Контур работает поверх Pilot Certification 1.5 и не ослабляет ограничения `LOCAL → SERVICE → 5 → 20 → 50 → 100`.

## 1. Commissioning checks

Добавлен сохраняемый отчёт инфраструктурной готовности с TTL и SHA-256 fingerprint. Проверяются:

- доступность БД;
- соответствие Alembic revision текущему head;
- запись, чтение и удаление контрольного файла в storage;
- Redis/local lock backend;
- свежесть worker heartbeat;
- целостность audit hash-chain;
- TOTP у Owner/Admin;
- HTTPS public URL;
- fake/live режим Telegram;
- свежесть локального backup;
- наличие `pg_dump` для PostgreSQL;
- политика ClamAV.

Отчёт получает статус `passed`, `warning` или `blocked`. Live-этап программы пилота нельзя начать с просроченным или заблокированным commissioning report.

## 2. Формальная программа пилота

Для выбранной кампании создаётся последовательность:

```text
local fake → service 1 → 5 → 20 → 50 → 100
```

Допустимые live-размеры намеренно ограничены значениями `1`, `5`, `20`, `50`, `100`. Произвольный масштаб через этот API не принимается.

Каждый этап:

- выполняется только после предыдущего принятого этапа;
- требует точного количества активных назначений;
- повторно проверяет commissioning, approval, Production Pilot readiness и campaign fingerprint;
- привязывается к конкретному завершённому `campaign_run`;
- сохраняет immutable evidence snapshot и его SHA-256;
- блокирует при `failed`, `cancelled`, `waiting_review` или неполной доставке;
- может требовать независимый sign-off другого Owner/Admin.

Для завершённой программы формируется JSON-акт приёмки с `payload_sha256` и результатом проверки evidence integrity.

## 3. Безопасный configuration bundle

Экспорт переносит структуру платформы, но не credentials. Поддерживаются:

- Telegram connections без token, `api_hash` и StringSession;
- destinations с исторической ссылкой на правила, но без действующего подтверждения;
- templates, campaigns и route links;
- media metadata и опционально сами файлы;
- blackouts;
- automation flows;
- AI provider metadata без API key;
- automation policies и knowledge base;
- integration metadata без секретной конфигурации.

После импорта применяется fail-safe состояние:

- connection — `draft`, без credentials;
- destination — выключен, не проверен, permission=`unverified`;
- template — inactive;
- campaign — `draft`, без approval и расписанного запуска;
- flow/provider/policy/integration/blackout — disabled;
- media повторно проходит MIME, magic bytes, SHA-256 и ClamAV policy.

ZIP проверяется на path traversal, symlinks, encrypted entries, дубликаты путей, незаявленные файлы, несовпадение SHA-256, неизвестные разделы, secret-like поля, повреждённые enum/числовые значения и превышение лимитов. Архив можно скачать с `Cache-Control: no-store` или удалить вместе с файлом в storage; удаление записывается в audit-chain.

Manifest подтверждает целостность содержимого, но не авторство. Для недоверенного канала доставки требуется внешняя подпись или доверенный artifact registry.

## 4. Web UI и API

В SPA добавлен 23-й раздел **«Ввод в эксплуатацию»**:

- запуск и история commissioning checks;
- создание программ пилота;
- состояние и доказательства каждого этапа;
- attach campaign run;
- независимый sign-off;
- скачивание акта приёмки;
- export/preview/import/download/delete configuration bundle.

Новые API-группы:

```text
/api/v1/commissioning
/api/v1/pilot/programs
/api/v1/configuration-bundles
```

Изменяющие действия доступны только Owner/Admin; чтение диагностических отчётов и программ остаётся tenant-scoped.

## 5. Database и deployment

Новый Alembic head:

```text
2a6f0104062a
```

Проверенный round-trip:

```text
1.5 e2f4a6c8d0b1
→ upgrade
1.6 2a6f0104062a
→ downgrade
1.5 e2f4a6c8d0b1
→ upgrade
1.6 2a6f0104062a
```

Добавлена настройка:

```text
TELEFLOW_COMMISSIONING_TTL_MINUTES=30
```

## 6. QA

Финальный release-QA:

```text
155 tests passed
8 isolated coverage shards
11 686 statements
2 575 missed
77.97% statement coverage
65% required gate
Alembic fresh upgrade/check/round-trip passed
HTTP smoke passed
Worker once passed
Audit-chain verify passed
Master-key rotation dry-run passed
Nginx/systemd/shell validation passed
```

Реальные Bot API, MTProto, Telegram Business, S3, Google Sheets, external AI, ClamAV daemon и public TLS по-прежнему требуют credentials и инфраструктуру владельца.
