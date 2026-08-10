# Чек-лист live-пилота TeleFlow Platform

## 1. Входные данные

До начала должны быть готовы:

- production/staging домен с HTTPS;
- PostgreSQL и Redis;
- отдельный рабочий Telegram-аккаунт владельца;
- собственные `api_id`/`api_hash`;
- тестовый Bot API token;
- Telegram Business профиль при использовании inbox;
- одна служебная группа и пять целевых групп;
- письменное/публичное основание разрешения для каждой группы;
- тексты, media и topic IDs;
- ответственный оператор и канал инцидентов.

Credentials вводит владелец через HTTPS-панель. Не передавать auth code, cloud password, session string или `.env` в чатах/тикетах.

## 2. Infrastructure gate

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps
docker compose exec api python scripts/doctor.py
curl -fsS https://panel.example.com/api/v1/health/live
curl -fsS https://panel.example.com/api/v1/health/ready
```

Проверить:

- migration завершён;
- API и worker healthy;
- worker heartbeat виден;
- Grafana/alerts настроены;
- backup создан и расшифровывается на отдельном host;
- owner сменил пароль и включил TOTP.

## 3. Fake gate

1. `TELEFLOW_TELEGRAM_FAKE_MODE=true` только в staging.
2. Создать connection/destination/template/campaign.
3. Preview должен быть valid.
4. Отправить campaign на approval.
5. Owner/admin принять решение; при four-eyes использовать второго пользователя.
6. Проверить, что run-now до approval получает 409.
7. Run now + worker.
8. Job получает `fake-*` message ID.
9. Pause/cancel/retry/audit работают.
10. После успешного fake gate staging можно переключить на live.

## 4. Bot API gate

1. Отключить fake mode.
2. Добавить бота в служебную группу.
3. Выдать право отправки без лишних admin permissions.
4. Добавить token в панели.
5. Health check должен вернуть identity.
6. Validate destination должен подтвердить write capability.
7. Отправить один текст.
8. Отправить одно изображение/документ.
9. Отправить в test forum topic.
10. Сверить Telegram message ID с job/audit.
11. Удалить право записи и убедиться, что destination отключается.

Стоп при неожиданном получателе, дубле или несовпадении preview.

## 5. MTProto gate

1. Владелец вручную вступает только в разрешённые группы.
2. В панели вводит phone, `api_id`, `api_hash`.
3. Завершает code/2FA challenge в пределах TTL.
4. Проверяет identity и discovered dialogs.
5. Не запускает ту же session во втором TeleFlow worker.
6. Создаёт кампанию на одну служебную группу.
7. Manual approval обязателен.
8. Проверяет text/media/topic.
9. Затем создаёт один проход по пяти группам.
10. Для первого пилота: последовательная очередь, 90–120 секунд, без повтора в тот же день.
11. Наблюдает Telegram и панель весь проход.

Интервал не является гарантией отсутствия ограничений. Правила группы, жалобы и ответы Telegram имеют приоритет.

Стоп при FloodWait, anti-spam, auth issue, ambiguous result или жалобе администратора. Не возобновлять до ручной проверки.

## 6. Telegram Business gate

1. Bot connection active.
2. Public base URL использует HTTPS.
3. В панели установить webhook.
4. Проверить `getWebhookInfo` и отсутствие last error.
5. Подключить бота к Telegram Business profile с минимальными правами.
6. Убедиться, что появился Business connection ID.
7. Создать automation policy в disabled state.
8. Добавить consent notice/fallback/stop/handoff words.
9. Включить `rule_based` provider.
10. Включить policy.
11. Написать из тестового аккаунта.
12. Проверить consent request.
13. Ответить «да» и пройти анкету.
14. Проверить Candidate profile.
15. Написать «оператор» и проверить handoff.
16. Ответить вручную из панели.
17. Написать «стоп» и проверить отключение AI.
18. Повторно доставить тот же update в test proxy и проверить дедупликацию.

## 7. External AI gate

1. Оценить условия обработки данных провайдера.
2. Выбрать HTTPS base URL и allowlisted model.
3. Создать provider disabled.
4. Выполнить test endpoint.
5. Проверить strict JSON response.
6. Проверить timeout/fallback.
7. Проверить prompt injection message.
8. Убедиться, что чужой conversation/tenant не попал в context.
9. Включить provider только для test policy.
10. Проверить tokens/latency/safety flags в interaction log.

## 8. Integration gate

### Webhook

- HTTPS endpoint;
- receiver проверяет `X-TeleFlow-Signature`;
- test event;
- retry after temporary 500;
- duplicate event ID обрабатывается idempotently получателем.

### Google Sheets

- отдельный service account;
- доступ только к нужной таблице;
- spreadsheet/range test;
- append test;
- revoke old key после пилота.

### CSV

- storage object создаётся;
- filename содержит event ID;
- нет перезаписи предыдущего event;
- backup включает exports при необходимости.

## 9. Privacy gate

1. Создать test conversation/candidate с consent.
2. Выполнить export.
3. Скачать и сверить данные.
4. Дождаться/смоделировать TTL и проверить удаление файла.
5. Выполнить delete.
6. Убедиться, что contact/body больше не раскрываются.
7. Audit должен содержать событие без PII.

## 10. Failure drills

Без провоцирования реального антиспама проверить mock/staging:

- SlowMode defer;
- FloodWait pause;
- anti-spam manual review;
- uncertain delivery;
- stale job lease;
- worker stop/heartbeat alert;
- outbox 500 → retry → success;
- outbox dead letter;
- backup restore;
- master/API key/token revoke procedure;
- organization emergency stop во время арендованного fake job;
- stale fingerprint после изменения template/destination;
- истечение approval request;
- critical notification read/ack;
- audit chain tamper detection на копии БД;
- master-key rotation dry-run на восстановленной копии backup.

## 11. Переход 5 → 20 → 50 → 100

Перед каждым расширением:

- предыдущий пакет принят;
- destinations имеют evidence/topic/hours/cooldown;
- нет жалоб или удалений;
- queue и worker stable;
- daily cap рассчитан;
- backup/alerts работают;
- оператор понимает stop procedure;
- approval policy выполнима текущим числом owner/admin;
- pending approval requests отсутствуют или обоснованы;
- audit chain verify возвращает valid.

Нельзя превращать расширение в бесконечную рассылку. Повтор публикации задаётся правилами каждой конкретной группы.

## 12. Акт пилота

Зафиксировать:

```text
Дата/окружение:
Version/commit/archive SHA:
Bot identity:
User identity:
Business connection ID:
Destinations и permission evidence:
Campaign run IDs:
Telegram message IDs:
Telegram update IDs:
Ошибки/ограничения:
Backup ID и restore result:
Ответственные:
Решение о следующем пакете:
```

## 13. Дополнительные gates 1.1

### Destination onboarding

1. Выполнить discovery уже доступных dialogs.
2. Убедиться, что платформа не вступила ни в одну новую группу.
3. Импортировать test CSV через preview/apply.
4. Проверить duplicate/invalid rows.
5. Подтвердить evidence только для реально разрешённых destinations.
6. Выполнить export и сверить timezone/window/cooldown.

### Individual window

1. Назначить test destination окно, начинающееся через несколько минут.
2. Убедиться, что worker перенёс `due_at` без Telegram request.
3. Проверить обычное и ночное окно.
4. После successful send убедиться, что cooldown блокирует ранний повтор.

### Visual flow

1. Создать flow message → question → choice → end/handoff.
2. Изменить порядок drag-and-drop.
3. Пройти flow тестовым пользователем.
4. Проверить age/phone/email validation.
5. Проверить skip-if-present.
6. Активировать revision и убедиться, что она стала read-only.

### A/B templates

1. Создать два разрешённых templates.
2. Установить B weight.
3. Сверить preview минимум на пяти destinations.
4. Повторить preview/run и убедиться в стабильности assignment.
5. Проверить snapshot body/media в jobs.
6. Сверить analytics breakdown; не считать delivery conversion найма.

### Browser sessions и PWA

1. Войти с двух тестовых browser profiles.
2. Отозвать одну session и проверить 401/refresh failure.
3. Выполнить revoke-others.
4. Установить PWA.
5. Отключить сеть и убедиться, что показывается только static offline page.
6. Проверить Cache Storage: API/auth/conversations/candidates отсутствуют.

### ClamAV

1. Проверить clean image/document.
2. В изолированном environment проверить EICAR test string/file.
3. Убедиться, что malware не записан в storage.
4. Остановить `clamd` и проверить fail-closed response.
5. Проверить audit events и отсутствие raw file body.


## 14. Governance & Resilience gate 1.2

### Approval policy

1. Создать минимум двух owner/admin для проверки four-eyes.
2. Включить `require_distinct_campaign_approver`.
3. Установить low test threshold, чтобы fake campaign считалась high-risk.
4. Установить два required approvals.
5. Operator/requester отправляет campaign на approval.
6. Попытка requester принять решение должна быть отклонена.
7. Первое независимое решение оставляет request в `pending` и показывает прогресс 1/2.
8. Второе независимое решение переводит request в `approved`.
9. Изменить шаблон или маршрут и убедиться, что approval отменён.
10. Создать новый request, искусственно истечь TTL и выполнить worker tick: статус `expired`, campaign `draft`, notification создано.

### Emergency stop

1. Создать approved fake campaign и due jobs.
2. Включить organization stop с фактической причиной.
3. Проверить красную полосу в UI.
4. Проверить, что pending/retry jobs получили `waiting_review` и `ORG_EMERGENCY_STOP`.
5. Проверить, что scheduler не создаёт новый run.
6. Попробовать worker cycle и убедиться, что fake gateway не вызван.
7. Выполнить resume.
8. Проверить повторный safety check и успешную доставку только при current approval.

### Notifications

1. Проверить unread/critical counters.
2. Открыть notification и отметить прочитанным.
3. Подтвердить critical notification отдельным действием.
4. Повторить одинаковое событие в пределах dedup window и проверить `occurrence_count`.
5. Проверить `notification.created` в outbox.

### Audit chain

При остановленных writers после upgrade:

```bash
python scripts/audit_chain.py backfill --include-system --yes
python scripts/audit_chain.py verify --include-system --json
```

Verify должен быть valid. Tamper test выполняется только на disposable копии БД: изменение защищённого поля обязано давать invalid. После теста копия уничтожается, production chain не «исправляется» backfill.

### Master-key rotation

На восстановленной копии backup:

1. Сформировать новый сильный key.
2. Выполнить dry-run.
3. Убедиться, что invalid/short key отклоняется.
4. Выполнить rotation с `--yes`.
5. Запустить приложение с новым key.
6. Проверить Telegram connection metadata, TOTP login, conversations/candidate contact, AI/integration config и privacy export.
7. Проверить audit verify и fake campaign.

## 15. Controlled Operations gate 1.3

### Permission expiry

- [ ] у каждой из пяти групп заполнено основание разрешения;
- [ ] задана дата следующей ревизии/окончания разрешения;
- [ ] искусственно истёкшее разрешение блокирует preflight, scheduler и worker;
- [ ] повторное подтверждение снимает старый campaign approval.

### Persisted preflight

- [ ] оператор видит per-destination blockers/warnings/fingerprint/batch;
- [ ] успешный отчёт истекает по TTL;
- [ ] scheduler создаёт новый отчёт перед run;
- [ ] удалённый media object и недавний дубль блокируют запуск.

### Staged rollout

- [ ] batch 1 активен, batch 2+ имеют `held`;
- [ ] worker не выбирает `held`;
- [ ] manual checkpoint раскрывает только один следующий пакет;
- [ ] `waiting_review` блокирует checkpoint;
- [ ] превышение error threshold не раскрывает следующий пакет;
- [ ] controlled abort отменяет остаток и аннулирует approval.

### Reconciliation

- [ ] тестовый ambiguous result не повторяется после connection resume;
- [ ] `confirmed_sent` требует message ID и обновляет cooldown;
- [ ] `confirmed_not_sent` создаёт один явный retry только при актуальных checks;
- [ ] `skipped` закрывает job без network call;
- [ ] все решения видны в audit и notifications.

## 16. Production Pilot gate 1.4

### Destination revalidation

- [ ] У каждой из пяти пилотных групп есть подтверждённое основание разрешения.
- [ ] Выполнена свежая Telegram-проверка через фактическое подключение.
- [ ] `validated_at` и `validation_expires_at` видны в панели.
- [ ] History содержит успешный live-result и capabilities.
- [ ] Тестовая группа без права писать корректно получает `write_forbidden` и отключается.
- [ ] Batch после искусственного/реального retry-after не продолжает остальные запросы.

### Operational calendar

- [ ] Создано тестовое one-time окно на служебную группу.
- [ ] Во время окна Telegram gateway не вызывается, job переносится.
- [ ] Создано weekly overnight окно в timezone организации.
- [ ] Проверены момент начала, участок после полуночи и точное окончание.
- [ ] Изменение и отключение окна видны в audit.

### Readiness report

- [ ] Worker heartbeat свежий.
- [ ] Connection health свежий в live mode.
- [ ] Approval и preflight актуальны.
- [ ] Все пять назначений имеют свежую validation и неистёкшее permission evidence.
- [ ] Readiness report не содержит blockers.
- [ ] Fake-mode warning отсутствует в live-среде.
- [ ] Истечение TTL действительно блокирует повторный запуск.
- [ ] Изменение шаблона/маршрута инвалидирует старый report.
- [ ] Scheduler без report ставит тестовую due-кампанию на паузу и создаёт уведомление.

### Допуск к расширению

- [ ] Первый staged batch доставлен и вручную сверён.
- [ ] Нет FloodWait/anti-spam/write-forbidden/uncertain events.
- [ ] Правила групп не нарушены, жалоб и удалений нет.
- [ ] Только после этого разрешено расширение 5 → 20.

## Сертификационная последовательность 1.5

### LOCAL

- fake mode включён;
- весь UI/API/worker workflow пройден без Telegram network calls;
- backup/restore и emergency stop проверены.

### SERVICE

- подключён отдельный рабочий аккаунт или тестовый бот;
- выбрана одна собственная служебная группа;
- Telegram validation свежая;
- выполнена одна реальная canary без autoretry;
- сообщение проверено вручную.

### FIVE

- пять групп имеют документированное разрешение и неистёкшую validation;
- staged rollout включён;
- первый пакет завершён без unresolved uncertain delivery;
- результат и critical notifications проверены оператором.

### TWENTY / FIFTY / HUNDRED

Перед каждым повышением:

- есть реальный завершённый run предыдущего масштаба в допустимом окне;
- failure percentage не превышает policy;
- нет FloodWait/anti-spam/auth errors;
- permissions, blackout и cooldown актуальны;
- создан новый assessment и fingerprint совпадает;
- Owner/Admin явно подтверждает следующий, а не произвольный этап.

При любом существенном инциденте включить emergency stop и понизить stage. Масштаб не восстанавливается автоматически.

## 17. Commissioning & Portability gate 1.6

### Commissioning

- [ ] Текущая Alembic revision совпадает с `2a6f0104062a`.
- [ ] Storage round-trip пройден, контрольный объект удалён.
- [ ] Worker heartbeat свежий, lock backend отвечает.
- [ ] Audit hash-chain возвращает `valid=true`.
- [ ] Owner/Admin используют TOTP согласно production policy.
- [ ] Public URL использует HTTPS.
- [ ] Есть свежий проверяемый backup и доступен `pg_dump` для PostgreSQL.
- [ ] ClamAV policy соответствует выбранному fail-closed/fail-open режиму.
- [ ] Commissioning report не содержит blockers и не истёк.

### Формальная программа

- [ ] Создан local fake stage и привязан завершённый fake campaign run.
- [ ] Evidence SHA-256 проверен до sign-off.
- [ ] Service stage использует одну собственную разрешённую группу и реальную canary/run.
- [ ] Каждый следующий этап имеет точный размер 5/20/50/100 и не пропускает предыдущий.
- [ ] Нет failed/cancelled/waiting_review и неполной доставки.
- [ ] Distinct sign-off выполнен другим Owner/Admin, когда политика включена.
- [ ] Итоговый acceptance report скачан, его `payload_sha256` сохранён и проверен.

### Перенос конфигурации

- [ ] Export ZIP не содержит tokens, `api_hash`, StringSession, passwords, API keys или approvals.
- [ ] SHA-256 файла сверён до передачи.
- [ ] Preview на целевой инсталляции не содержит security errors.
- [ ] После import connections остаются без credentials, destinations disabled/unverified, campaigns draft.
- [ ] Media повторно проверены, если включались в архив.
- [ ] Credentials, permissions, validation, approval и readiness созданы заново на целевой среде.
- [ ] Configuration bundle удалён после использования либо помещён в контролируемое защищённое хранилище.

Успех автоматических тестов не заменяет этот gate. Live-этапы отмечаются принятыми только после фактической проверки Telegram-доставки в разрешённых группах.

## 18. Artifact Trust gate 1.7

- [ ] Текущая Alembic revision совпадает с `3b7d9f1a2c4e`.
- [ ] Production policy установлена в `require_trusted`.
- [ ] Создан default Ed25519 key с локальной private part.
- [ ] Default key явно отмечен trusted.
- [ ] Public PEM сохранён отдельно, fingerprint сверен на второй административной станции.
- [ ] Тестовый configuration bundle имеет `valid_trusted`.
- [ ] Тот же bundle проходит `scripts/verify_artifact.py --require-trusted`.
- [ ] Support bundle подписан тем же ожидаемым signer.
- [ ] Pilot acceptance report содержит проверяемую подпись.
- [ ] Проверена блокировка tampered и revoked artifacts.
- [ ] При переносе между инсталляциями public key импортирован только после независимой сверки fingerprint.
- [ ] Проведён revoke/new-key drill либо зафиксирован план и ответственные.

## 19. Recovery Assurance gate 1.8

До начала live-пилота:

- [ ] configured RPO/RTO policy;
- [ ] trusted default Ed25519 signing key;
- [ ] production age recipient настроен;
- [ ] age private identity хранится отдельно;
- [ ] создан новый encrypted backup;
- [ ] artifact получил `verified`;
- [ ] restore drill receipt имеет `passed`;
- [ ] фактическая длительность соответствует RTO;
- [ ] retained verified count соответствует policy;
- [ ] S3 recovery/replication проверены отдельно, если используются;
- [ ] commissioning `recovery_assurance` не содержит blockers;
- [ ] emergency stop остаётся включённым до завершения recovery проверки.

Для PostgreSQL metadata-only недостаточно: перед production acceptance выполните restore в disposable database и application smoke.

## 20. Operational SLO gate 2.2

Перед service/live этапом:

- [ ] Alembic revision совпадает с `8a2c4e6f0b3d`.
- [ ] Worker heartbeat свежий.
- [ ] SLO policy проверена Owner/Admin и не была ослаблена ради запуска.
- [ ] `gate_publishing=true`; для production изменений также `gate_changes=true`.
- [ ] Создан свежий assessment с совпадающим policy SHA-256.
- [ ] Assessment не содержит blockers.
- [ ] Error budget ниже critical threshold.
- [ ] Нет просроченной очереди.
- [ ] Нет нерешённых `waiting_review` сверх policy.
- [ ] Все critical incidents разрешены либо находятся в допустимом policy количестве.
- [ ] Prometheus alerts и Grafana panels проверены.
- [ ] При каждом следующем staged checkpoint assessment пересоздан, если истёк TTL или изменилась policy.

SLO gate не подтверждает отсутствие ограничений Telegram и не заменяет разрешение каждой группы, canary, preflight, approval и ручной checkpoint.

## 21. Execution fencing и active/standby 2.3

- [ ] Alembic head совпадает с `9b3d5f7a1c4e`.
- [ ] Primary и standby используют одну PostgreSQL.
- [ ] Site keys уникальны.
- [ ] Primary отображается active, standby — online.
- [ ] Standby scheduler не создаёт jobs.
- [ ] Canary от primary имеет реальный Telegram message ID.
- [ ] Failover request переводит source в draining.
- [ ] Автор запроса не может подтвердить собственный failover.
- [ ] Processing/uncertain attempts блокируют переключение.
- [ ] После approval epoch увеличивается.
- [ ] Старый worker не может пройти Safety Engine.
- [ ] Canary после переключения отправляется только target site.
- [ ] Авария до network marker допускает safe retry.
- [ ] Авария после network marker требует ручной сверки.
- [ ] Alerts stale lease/offline site/open failover/uncertain attempt доставляются оператору.

## Continuity checklist 2.4

- [ ] На active и standby установлена одна версия 2.5.0.
- [ ] Alembic head обеих площадок — `bd5f7a9c3e6f`.
- [ ] Critical config SHA-256 и release payload SHA-256 совпадают.
- [ ] Heartbeat обеих площадок актуален.
- [ ] Нет processing jobs и unresolved delivery attempts.
- [ ] Выполнена zero-network simulation.
- [ ] Simulation принята независимым Owner/Admin.
- [ ] Maintenance window согласовано.
- [ ] Live failover подтверждён вторым администратором.
- [ ] Standby получила новый fencing epoch.
- [ ] Выполнена контрольная canary только в собственной служебной группе.
- [ ] Failback подтверждён вторым администратором.
- [ ] Primary получила новый fencing epoch.
- [ ] RTO не превышает policy.
- [ ] Event chain и evidence SHA-256 подтверждены.
- [ ] Evidence принято независимым Owner/Admin.

## Capacity checklist перед расширением пилота

- [ ] Capacity Policy включена.
- [ ] Admission и dispatch gates включены.
- [ ] Последний assessment актуален и не содержит structural blockers.
- [ ] Estimated drain time укладывается в принятую операционную норму.
- [ ] Ready/processing limits соответствуют фактическому числу workers.
- [ ] Minute/hour budgets не превышают подтверждённый canary-профиль.
- [ ] Первый маршрут использует staged rollout.
- [ ] Checkpoint между пакетами включён.
- [ ] Нет unresolved `waiting_review` и открытых critical incidents.
- [ ] После изменения policy выполнен новый assessment.
- [ ] Canary выполнена в собственной разрешённой служебной группе.
