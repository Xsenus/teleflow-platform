# Ввод в эксплуатацию и перенос конфигурации — TeleFlow Platform 1.6

> Примечание для 1.7: configuration bundles и pilot acceptance reports получили Ed25519-подписи. Разделы ниже описывают базовый формат 1.6; актуальная trust-модель приведена в [ARTIFACT_TRUST.md](ARTIFACT_TRUST.md).

## 1. Назначение релиза

Версия 1.6 закрывает разрыв между «код установлен» и «система допущена к реальной работе». Она добавляет три взаимосвязанных контура:

1. **Commissioning checks** — сохраняемая диагностика инфраструктуры и эксплуатационных настроек.
2. **Pilot program** — формальный маршрут `fake mode → 1 → 5 → 20 → 50 → 100` с доказательствами и отдельным решением по каждому этапу.
3. **Configuration bundle** — перенос справочной конфигурации между ПК, staging и сервером без токенов, Telegram-сессий, паролей и прежних утверждений.

Эти механизмы не дают разрешение на публикацию сами по себе. Перед live-этапом по-прежнему требуются действующие правила группы, свежее Telegram-подтверждение доступа, approval кампании, preflight и Production Pilot readiness.

## 2. Диагностика инфраструктуры

Отчёт создаётся через веб-панель или API и хранится в таблице `commissioning_check_runs`. Каждый отчёт содержит:

- общий статус `passed`, `warning` или `blocked`;
- машиночитаемый список проверок;
- blockers и warnings;
- SHA-256 fingerprint результата;
- автора, время создания и срок действия;
- безопасную сводку без секретов.

Проверяются:

- подключение к БД;
- совпадение текущей Alembic revision с migration head;
- запись, чтение, SHA-проверка и удаление контрольного файла в storage;
- Redis/local lock backend;
- свежесть worker heartbeat;
- целостность audit hash-chain;
- TOTP у активных Owner/Admin;
- HTTPS public base URL;
- fake/live режим Telegram gateway;
- наличие свежего локального backup;
- наличие `pg_dump` для PostgreSQL;
- политика ClamAV.

Контрольный файл создаётся под случайным ключом и удаляется в `finally`. Его содержимое не сохраняется в БД или audit.

### Статус проверки

- `passed` — проверка выполнена и соответствует ожидаемой политике;
- `warning` — допустимо для локального этапа, но требует внимания перед production;
- `blocked` — live-пилот начинать нельзя.

В development/test отсутствие Alembic revision при разрешённом `auto_create_schema` является предупреждением. В production это blocker.

## 3. Программа пилота

Программа привязана к одной кампании и создаёт последовательность этапов:

| Порядок | Тип | Назначений | Назначение |
|---:|---|---:|---|
| 0 | Local fake | 1 | Проверка UI, scheduler, worker, audit и доказательств без Telegram network call |
| 1 | Live service | 1 | Одна служебная группа с явным разрешением |
| 2 | Live | 5 | Первый ограниченный пакет |
| 3 | Live | 20 | Расширенный пилот |
| 4 | Live | 50 | Предproduction масштаб |
| 5 | Live | 100 | Целевой маршрут, если это соответствует правилам групп |

Список live-размеров выбирается только из сертифицированной лестницы `1`, `5`, `20`, `50`, `100`, должен быть уникальным, возрастающим и начинаться с `1`.

### Состояния программы

```text
draft → active → completed
             ↘ paused
             ↘ cancelled
```

### Состояния этапа

```text
pending → ready → running → awaiting_signoff → passed
                                      ↘ failed
pending/ready/running/awaiting_signoff → skipped при отмене программы
```

### Условия начала этапа

Перед стартом повторно проверяются:

- этап является текущим;
- предыдущий этап принят;
- количество активных назначений кампании точно соответствует этапу;
- local-этап выполняется в fake mode, live-этап — только при отключённом fake mode;
- commissioning report актуален и не blocked;
- approval кампании актуален;
- Production Pilot readiness актуален;
- campaign fingerprint не изменился.

### Доказательства запуска

К этапу привязывается конкретный `campaign_run`. В immutable evidence snapshot входят:

- ID программы, этапа, кампании и запуска;
- campaign fingerprint;
- количество jobs и итоговые статусы;
- ID назначений;
- Telegram message ID, когда он подтверждён;
- error code и attempt count;
- ссылки на commissioning/readiness/preflight reports;
- audit-chain head;
- blockers;
- операторская заметка;
- SHA-256 всего evidence payload.

После первой успешной привязки `campaign_run` evidence становится неизменяемым. Перед sign-off платформа пересчитывает SHA-256 и проверяет контекст `program_id`, `stage_id`, `campaign_id`, `campaign_run_id`, размер маршрута и тип fake/live этапа. Повреждённое или подменённое evidence блокирует решение, а акт приёмки явно показывает `evidence_integrity.valid=false`. Отклонённый этап можно запустить повторно только как новый evidence cycle; прежние run ID, hash и решение остаются трассируемыми через audit events.

Тексты сообщений, Telegram tokens, StringSession, phone/email кандидатов и сырой inbound update в доказательства не включаются.

### Независимая приёмка

Для live-этапов можно включить `require_distinct_signoff`. Тогда пользователь, начавший этап, не может сам подтвердить его успешное завершение. Решение принимает другой Owner/Admin.

Этап нельзя принять как `passed`, если:

- run не завершён;
- количество jobs отличается от размера этапа;
- есть `failed`, `cancelled` или `waiting_review`;
- не все сообщения подтверждены как `sent`;
- audit-chain не прошла проверку.

### Акт приёмки

Для программы формируется JSON-отчёт с собственным `payload_sha256`. В 1.7 payload дополнительно подписывается Ed25519. Подпись пригодна для проверки происхождения внутри trust-модели TeleFlow, но не заменяет WORM-хранилище или юридически квалифицированную электронную подпись.

## 4. Безопасный configuration bundle

Configuration bundle переносит только справочную конфигурацию и, по отдельному выбору, медиафайлы. Credential-поля исключаются схемой, однако тексты шаблонов, заметки, system prompt и статьи базы знаний являются пользовательским свободным текстом. Перед передачей архива их необходимо вручную проверить; этот факт фиксируется в `safety_contract.free_text_review_required`.

### Экспортируемые сущности

- Telegram connections без credentials;
- destinations и ссылки на прежнее основание разрешения;
- media metadata и опционально сами файлы;
- message templates;
- campaigns и route links;
- publishing blackouts;
- automation flows;
- AI provider metadata без API key;
- automation policies;
- knowledge base articles;
- integration metadata без config/secret.

### Какие credential-поля никогда не экспортируются

- Bot API token;
- `api_id`/`api_hash`, MTProto challenge и StringSession;
- Telegram cloud password;
- JWT, refresh tokens и master key;
- TOTP secret;
- AI API key;
- integration HMAC secret/service-account JSON;
- S3 credentials;
- TLS private key;
- approval decisions;
- действующий permission confirmation;
- delivery jobs, message texts и inbound correspondence;
- контакты кандидатов.

### Fail-safe состояние после импорта

Импортированные объекты не могут начать работу автоматически:

- connection: `draft`, без credentials, manual approval обязателен;
- destination: `enabled=false`, `validated=false`, permission=`unverified`;
- template: inactive;
- campaign: `draft`, без approval и без автоматического запуска;
- flow/provider/policy/integration: disabled;
- blackout: disabled;
- медиа повторно проходит type/magic/SHA-256 и ClamAV policy.

После переноса администратор должен повторно авторизовать Telegram, проверить каждую группу, подтвердить правила, проверить шаблоны и создать новое approval.

### Формат архива

```text
teleflow-config-*.zip
├── bundle.json
├── manifest.json
└── media/...              # только при include_media=true
```

`manifest.json` содержит SHA-256 каждого заявленного файла. Импорт блокирует:

- абсолютные пути и `..`;
- символические ссылки;
- повторяющиеся пути, включая различие только регистром;
- зашифрованные ZIP entries;
- незаявленные или отсутствующие файлы;
- несовпадение SHA-256;
- превышение общего размера или количества файлов/сущностей;
- неизвестные разделы schema v1;
- повторяющиеся entity refs;
- secret-like поля;
- недопустимые enum/числовые значения;
- повреждённый JSON или ZIP.

Configuration bundle исключает известные credential-поля, однако свободный текст шаблонов, заметок, статей и сценариев создаётся людьми и может содержать случайно вставленный секрет. Поэтому manifest явно содержит `free_text_review_required=true`: перед передачей архива оператор обязан просмотреть такие поля и при необходимости создать очищенную копию.

В базовом формате 1.6 manifest обеспечивал только целостность. В 1.7 canonical manifest подписывается Ed25519; перед импортом всё равно необходимо независимо сверить public fingerprint и требовать `valid_trusted`.

## 5. API

### Commissioning

```text
GET  /api/v1/commissioning
GET  /api/v1/commissioning/latest
POST /api/v1/commissioning/run
```

Запуск доступен Owner/Admin. Просмотр — всем аутентифицированным пользователям организации.

### Pilot programs

```text
GET  /api/v1/pilot/programs
POST /api/v1/pilot/programs
GET  /api/v1/pilot/programs/{program_id}
POST /api/v1/pilot/programs/{program_id}/stages/{stage_id}/refresh
POST /api/v1/pilot/programs/{program_id}/stages/{stage_id}/start
POST /api/v1/pilot/programs/{program_id}/stages/{stage_id}/attach-run
POST /api/v1/pilot/programs/{program_id}/stages/{stage_id}/signoff
POST /api/v1/pilot/programs/{program_id}/cancel
GET  /api/v1/pilot/programs/{program_id}/acceptance-report
```

### Configuration bundles

```text
GET  /api/v1/configuration-bundles
POST /api/v1/configuration-bundles/export
POST /api/v1/configuration-bundles/preview
POST /api/v1/configuration-bundles/import?conflict_mode=skip|rename
GET    /api/v1/configuration-bundles/{bundle_id}/download
DELETE /api/v1/configuration-bundles/{bundle_id}
```

Импорт и экспорт доступны Owner/Admin. Download tenant-scoped и фиксируется в audit. Удаление уничтожает ZIP в storage, удаляет metadata-строку и также фиксируется в audit-chain.

## 6. Рекомендуемый порядок ввода

```text
1. Развернуть 1.6 и выполнить `alembic upgrade head`.
2. Запустить doctor и commissioning checks.
3. Устранить blockers.
4. Создать/проверить кампанию в fake mode.
5. Создать pilot program.
6. Завершить и принять local fake stage.
7. Перевести отдельное Telegram-подключение в live mode.
8. Проверить одну служебную группу и её разрешение.
9. Завершить live stage 1 и получить независимый sign-off.
10. Перейти к 5, затем 20, 50 и 100 только после отдельной приёмки.
11. Перед переносом ПК → сервер создать configuration bundle.
12. На сервере выполнить preview/import, затем заново авторизовать credentials и подтвердить разрешения.
```

## 7. Rollback и резервирование

Перед обновлением 1.5 → 1.6:

1. Остановите scheduler/worker.
2. Создайте DB + storage backup.
3. Проверьте checksum backup.
4. Выполните `alembic upgrade head`.
5. Запустите `doctor` и commissioning checks.

Технический downgrade к `e2f4a6c8d0b1` проверяется release-QA. После создания программ пилота и configuration bundles предпочтителен rollback через pre-upgrade backup, поскольку 1.5 не понимает новые сущности и доказательства.

## 8. Ограничения

Версия 1.6 не может сама подтвердить внешние факты без инфраструктуры владельца:

- действительность Bot API token и MTProto credentials;
- наличие разрешения администраторов конкретных групп;
- фактическую доставку в live Telegram;
- работу внешнего AI, Google Sheets, S3 и ClamAV daemon;
- публичный TLS и restore drill на production-host.

Эти проверки выполняются по [PILOT_CHECKLIST.md](PILOT_CHECKLIST.md).
