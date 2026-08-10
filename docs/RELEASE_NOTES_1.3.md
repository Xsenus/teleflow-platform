# TeleFlow Platform 1.3.0 — Controlled Operations

Дата релиза: **6 августа 2026 года**

Версия 1.3.0 развивает governance-контур 1.2 и добавляет управляемый пилот публикаций. Цель релиза — не увеличить объём автоматической отправки, а не допустить запуск с просроченным разрешением, недавним дубликатом, непроверенным маршрутом или неоднозначным результатом предыдущей доставки.

## Главное

- сохраняемый preflight-отчёт по каждому назначению;
- обязательный свежий автоматический preflight непосредственно перед созданием run;
- срок действия подтверждённого разрешения группы;
- предупреждение о приближении срока и блокировка после истечения;
- SHA-256 content fingerprint снимка сообщения;
- duplicate guard по назначению и настраиваемому периоду;
- пакетный staged rollout с физически удерживаемыми jobs;
- ручной checkpoint или автоматический переход при допустимом качестве пакета;
- остановка следующего пакета при превышении порога ошибок;
- ручная сверка неоднозначной доставки без небезопасного автоматического retry;
- журналируемое подтверждение «отправлено», «точно не отправлено» или «пропустить»;
- защита неоднозначного результата от campaign cancel, staged abort и connection revoke;
- остановка текущего staged run с отменой ожидающих jobs и снятием утверждения;
- веб-интерфейс для preflight, runs, checkpoints, held jobs и reconciliation.

## Модель запуска 1.3

```text
Черновик
  → preview
  → persisted preflight
  → approval request
  → independent approval(s)
  → run-now / scheduler
  → fresh scheduler preflight
  → batch 1 pending
  → batches 2..N held
  → worker safety check
  → checkpoint / automatic batch release
  → reconciliation of uncertain result when needed
  → completion or controlled abort
```

Preflight при утверждении и preflight при фактическом запуске являются разными снимками. Это принципиально: за время между ними могут истечь разрешение, измениться cooldown, появиться недавняя идентичная публикация или исчезнуть media object.

## Изменения схемы

Alembic head: `b7c9d2e4f6a8`.

Добавлены:

- `campaign_preflight_reports`;
- permission review/expiry fields у destinations;
- rollout policy fields у campaigns;
- batch/checkpoint fields у campaign runs;
- `held`, batch number, content fingerprint и reconciliation fields у delivery jobs.

Технический round-trip релиза:

```text
1.3 b7c9d2e4f6a8
→ downgrade
1.2 f4a6b8c12d34
→ upgrade
1.3 b7c9d2e4f6a8
```

Production rollback после появления данных 1.3 всё равно должен выполняться через проверенный pre-upgrade backup.

## Совместимость и ограничения

Сохранены все ограничения предыдущих версий:

- нет автоматического вступления в группы;
- нет сбора участников;
- нет массовых личных сообщений;
- нет ротации аккаунтов или прокси;
- нет обхода FloodWait, slow mode или anti-spam;
- нет скрытой модификации текста для обхода фильтров;
- публикация возможна только в назначения с зафиксированным основанием разрешения.

`duplicate_guard_minutes=0` отключает локальную защиту от дублей, но не отменяет Telegram limits и правила группы. Для пилота рекомендуется оставить защиту включённой.

## QA

- 107 автоматических тестов;
- statement coverage: **75,90%** (8862 statements, 2136 missed);
- coverage gate: 65%;
- Python compileall и JavaScript syntax checks;
- fresh Alembic upgrade и clean schema check;
- migration round-trip 1.3→1.2→1.3;
- HTTP login/CSRF/dashboard/logout smoke;
- audit-chain verification;
- master-key rotation dry-run;
- isolated worker cycle.

Реальные Bot API/MTProto публикации, Telegram Business webhook, production TLS, Docker image build и целевой browser acceptance требуют credentials и инфраструктуры владельца и не выдаются за пройденные в локальном release-QA.
