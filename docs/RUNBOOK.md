# Runbook оператора TeleFlow Platform 2.1

## 1. Начало смены

1. Открыть «Обзор».
2. Убедиться, что worker online.
3. Проверить connections: нет `error`, `paused`, `manual review`.
4. Проверить очередь `waiting_review` и failed jobs.
5. Проверить Telegram Business webhook/status.
6. Проверить новые handoff-диалоги.
7. Проверить outbox dead/retry.
8. Убедиться, что последний backup свежий.

## 2. Подготовка публикации

1. Проверить правила группы и evidence.
2. Указать правильный chat/topic ID.
3. Проверить разрешённые часы и cooldown.
4. Проверить template/media.
5. Создать draft campaign.
6. Открыть preview.
7. Устранить blockers.
8. Сверить порядок и рассчитанное время.
9. Утвердить только после ручной проверки.

## 3. Во время кампании

- не запускать параллельный worker с той же MTProto session;
- наблюдать status jobs;
- не редактировать Telegram message вручную, если нужно сохранить полную трассировку;
- при жалобе немедленно pause campaign/connection;
- не повышать interval/caps без анализа правил.

## 4. Waiting review

1. Поставить campaign/connection на паузу, если это ещё не сделано.
2. Открыть конкретный Telegram chat.
3. Найти сообщение по времени/text snapshot.
4. Если сообщение существует — не выполнять retry; зафиксировать результат в incident/audit process.
5. Если сообщения точно нет и ограничений нет — выполнить явный retry.
6. Если проверить невозможно — cancel job и согласовать ручную публикацию.

## 5. FloodWait/anti-spam

- не менять proxy/account ради продолжения;
- не запускать другой connection как обход;
- сохранить server wait/error;
- проверить аккаунт и правила групп;
- проверить жалобы/удаления;
- возобновить только после истечения wait и явного acknowledgement;
- при anti-spam оставить paused до решения владельца.

## 6. Работа с диалогами

- при handoff назначить оператора;
- открыть full body только при необходимости;
- отвечать из панели для сохранения audit;
- не копировать контакты в незащищённые чаты;
- изменить candidate status после контакта;
- закрыть conversation после завершения;
- выполнить privacy request при обращении пользователя.

## 7. AI policy

Перед включением:

- consent notice согласован;
- fallback и handoff keywords заполнены;
- active hours корректны;
- daily auto-reply cap задан;
- KB не содержит секретов;
- provider test проходит;
- policy сначала проверена на test Business connection.

## 8. Outbox

- `retry`: проверить last error и endpoint;
- `dead`: исправить endpoint, убедиться в idempotency получателя, затем manual retry;
- webhook receiver должен хранить event ID;
- Google key ротировать отдельно, не вставлять новый secret в audit/comment;
- CSV object использовать как event snapshot, не как изменяемую общую таблицу.

## 9. Конец смены

1. Проверить незавершённые campaigns.
2. Проверить `waiting_review`/dead queues.
3. Передать handoff-диалоги.
4. Зафиксировать incidents.
5. Убедиться, что нет временно раскрытых contact/export files.
6. Проверить schedule следующего запуска.

## 10. Массовый импорт и discovery

1. Выбрать connection.
2. Сначала выполнить preview/discovery.
3. Проверить title/chat/topic и отсутствие invite links.
4. Проверить permission evidence отдельно для каждой группы.
5. Применить import.
6. Запустить validation.
7. Установить individual timezone/window/cooldown.
8. Не включать destination в campaign до подтверждения.

## 11. Visual flow

1. Создать draft flow.
2. Разместить steps drag-and-drop или кнопками порядка.
3. Проверить terminal node и собираемые fields.
4. Сохранить и прогнать тестовый Business-dialog.
5. Проверить encrypted contacts и handoff.
6. Только после этого активировать revision и policy.

## 12. A/B campaign

1. Проверить оба templates и media.
2. Установить вес B.
3. Открыть preview и выборочно сверить assignment.
4. Утвердить campaign после проверки обоих вариантов.
5. После запуска смотреть delivery breakdown.
6. Не интерпретировать delivery success как candidate conversion без CRM attribution.

## 13. Security и media

- в начале смены проверить собственные active sessions;
- при неизвестном устройстве немедленно revoke и сменить password;
- при malware block не загружать переименованную копию;
- при ClamAV outage следовать fail-closed policy и incident process;
- не отключать scanner ради срочной публикации без решения owner/security.

## 14. Утверждение кампаний 1.2

1. Operator завершает preview и исправляет blockers.
2. Проверяет точный текст A/B, media, порядок групп, topics, timezone и due times.
3. Создаёт запрос утверждения с содержательным комментарием.
4. Owner/admin сверяет permission evidence каждой группы и принимает решение.
5. При включённом four-eyes requester не должен утверждать собственный запрос.
6. При high-risk route дождаться всех требуемых независимых approvals.
7. Проверить, что UI показывает статус `approved`, прогресс полностью заполнен и TTL не истёк.
8. Только затем запускать кампанию.

Если после решения изменены шаблон, маршрут, расписание или правила группы, прежнее утверждение больше не действует. Не пытаться запускать через прямой API: backend и worker блокируют stale fingerprint.

## 15. Центр уведомлений

В начале смены:

- открыть «Уведомления»;
- прочитать все critical/warning events;
- отдельно подтвердить critical события после фактической проверки;
- проверить `occurrence_count` — одно уведомление может представлять несколько повторов;
- перейти к связанной campaign/connection/destination/job;
- не использовать «прочитано» как замену acknowledgement.

FloodWait, anti-spam, auth loss и ambiguous delivery требуют расследования, а не автоматического retry.

## 16. Аварийный стоп организации

Нажать «Аварийный стоп», если:

- сообщение ушло в неожиданный чат;
- обнаружен возможный дубль;
- изменились права/правила группы во время запуска;
- Telegram вернул ограничение аккаунта;
- есть подозрение на утечку token/session;
- worker или маршрут ведут себя непредсказуемо.

После остановки:

1. Зафиксировать причину и время.
2. Проверить текущие Telegram-чаты вручную.
3. Разобрать jobs со статусом `waiting_review` и кодом `ORG_EMERGENCY_STOP`.
4. Устранить первопричину.
5. Повторно validate destinations/connections.
6. При изменении защищённых данных получить новое утверждение.
7. Подтвердить critical notification.
8. Выполнить resume и наблюдать первый job.

## 17. Проверка audit chain

Планово и после security incident:

```bash
python scripts/audit_chain.py verify --include-system --json
```

При `valid=false`:

- не выполнять backfill;
- остановить изменяющие операции;
- сохранить forensic backup;
- сравнить БД с off-site backup/immutable logs;
- зафиксировать first error entry/sequence/hash;
- действовать по `INCIDENT_RESPONSE.md`.

Backfill используется только один раз после штатного обновления legacy 1.1:

```bash
# API/worker остановлены
python scripts/audit_chain.py backfill --include-system --yes
python scripts/audit_chain.py verify --include-system --json
```

## 18. Preflight 1.3

1. Перед запросом approval откройте «Предпроверка».
2. Устраните каждый blocker; warning не скрывайте и оцените отдельно.
3. Проверьте срок разрешения, rules URL/основание, тему форума, media и content fingerprint.
4. После approval не считайте старый отчёт гарантией запуска: scheduler создаст новый.
5. Если автоматический preflight поставил кампанию на паузу, сначала устраните причину, затем создайте новый approval request.

## 19. Staged rollout и checkpoint

Для первого live-запуска используйте batch size 1 или 5 и `rollout_require_checkpoint=true`. После завершения пакета:

1. Откройте Telegram и проверьте каждое фактическое сообщение.
2. Убедитесь, что нет удалений, жалоб, неожиданных тем и дублей.
3. Разберите все `waiting_review`; checkpoint при них заблокирован.
4. Сопоставьте sent/failed/skipped/cancelled с порогом кампании.
5. Введите содержательный комментарий и разрешите следующий пакет либо выполните controlled abort.

Не используйте checkpoint как формальную кнопку. При любом неизвестном ограничении остановите run и organization publishing.

## 20. Ручная сверка доставки

При `DELIVERY_RESULT_UNCERTAIN`:

1. Не нажимайте retry и не создавайте новую кампанию с тем же текстом.
2. Откройте точную группу и topic, найдите сообщение по времени/тексту.
3. Если найдено — выберите «Отправлено», укажите Telegram message ID.
4. Если отсутствует — повторно проверьте соединение, разрешение и approval, затем выберите «Не отправлено».
5. Если доказать результат нельзя — выберите «Пропустить» и согласуйте дальнейшее действие отдельно.
6. Зафиксируйте проверяемое объяснение без секретов и лишних персональных данных.

## 21. Production Pilot 1.4

Перед live-запуском:

1. Откройте **Production Pilot**.
2. Убедитесь, что worker свежий, а Telegram-подключение активно.
3. Проверьте список «Требует внимания».
4. Запустите пакетную повторную проверку просроченных назначений небольшими партиями.
5. Для `write_forbidden` не включайте группу повторно без проверки её правил и прав аккаунта/бота.
6. Проверьте срок permission evidence и основание разрешения.
7. Проверьте операционный календарь и локальные timezone.
8. Создайте readiness report для кампании.
9. Раскройте blockers и warnings; blocked запускать нельзя.
10. После утверждения используйте staged rollout и ручной checkpoint первого пакета.

## 22. Операционные blackout-окна

Разовое окно используйте для обслуживания, согласованной паузы или конкретного события. Weekly — для постоянных тихих часов.

Перед сохранением проверьте:

- scope;
- выбранное подключение/назначение;
- IANA timezone;
- дни недели;
- пересечение полуночи;
- причину, понятную следующей смене.

Во время активного окна job остаётся ожидающим и переносится на `defer_until`. Не выполняйте ручной retry, чтобы «обойти» календарь; сначала явно измените или отключите правило с audit-записью.

## 23. Просроченный readiness у scheduler

Если автоматическая кампания поставлена на паузу с `campaign.readiness_stale_blocked`:

1. не возобновляйте её вслепую;
2. проверьте, почему report истёк или fingerprint изменился;
3. выполните destination revalidation;
4. создайте новый preflight/readiness;
5. при необходимости заново утвердите кампанию;
6. только затем возобновите расписание.

## Pilot Certification 1.5

### Повышение этапа

1. Убедиться, что emergency stop выключен и нет `waiting_review`.
2. Проверить worker heartbeat, Telegram health и свежесть validation назначений.
3. На этапе `SERVICE` выполнить одну реальную canary в собственной служебной группе.
4. Выполнить фактический staged run предыдущего масштаба и закрыть critical notifications.
5. Открыть **Production Pilot → Сертификация масштаба** и создать assessment следующего этапа.
6. Устранить blockers; warnings рассмотреть вручную.
7. Owner/Admin вводит показанную точную confirm-фразу и повышает только на следующий этап.
8. Снова создать readiness/preflight перед live-запуском.

Нельзя повышать этап только потому, что fake mode и локальные тесты успешны.

### Аварийное понижение

При FloodWait, anti-spam, серии ошибок, жалобах или ошибочном маршруте:

1. включить organization emergency stop;
2. разрешить все uncertain deliveries;
3. понизить pilot stage до безопасного уровня;
4. проверить, что несовместимые campaigns/jobs приостановлены;
5. создать support bundle;
6. сохранить audit verification result и incident timeline;
7. возобновлять работу только через новую canary, assessment и readiness.

### Canary

Использовать только собственную или явно разрешённую служебную группу. Canary не повторяется автоматически. При uncertain result сначала вручную проверить чат; новый запрос до сверки не выполнять.

### Support bundle

1. Создать bundle в Production Pilot.
2. Сверить SHA-256 и срок действия.
3. Скачать один раз по защищённому соединению.
4. Передать только назначенному специалисту.
5. После получения удалить bundle вручную либо дождаться TTL.
6. Не прикладывать отдельно `.env`, базу, логи с текстами или Telegram session.

## 24. Ввод в эксплуатацию и перенос 1.6

### Commissioning перед пилотом

1. Остановите изменения конфигурации и убедитесь, что emergency stop отражает фактическое состояние.
2. Откройте раздел **«Ввод в эксплуатацию»** и запустите commissioning check.
3. Для каждого blocker устраните первопричину: migration, storage, worker, lock backend, audit-chain, TOTP, HTTPS, backup или ClamAV policy.
4. Warning допустим только после осознанной оценки; для production не трактуйте warning как доказательство готовности.
5. Не используйте просроченный отчёт: создайте новый после изменения инфраструктуры.

### Формальная программа пилота

1. Создайте программу для одной уже подготовленной кампании.
2. Используйте только официальную лестницу `1 → 5 → 20 → 50 → 100`; пропуск этапа не допускается.
3. Начните local fake stage и выполните конкретный campaign run.
4. Привяжите завершённый run к этапу и проверьте evidence hash.
5. Для live-этапа отключите fake mode, пройдите commissioning/readiness/preflight и используйте только разрешённые группы.
6. Другой Owner/Admin выполняет sign-off, когда включено разделение ролей.
7. После последнего этапа скачайте JSON-акт, проверьте `payload_sha256` и сохраните его в доверенном внутреннем хранилище.

### Перенос ПК → сервер

1. На исходной инсталляции создайте configuration bundle без media либо с media только при необходимости.
2. Сверьте SHA-256, скачайте по HTTPS и удалите bundle после подтверждённой передачи.
3. На сервере выполните preview. Любой security error разбирайте; не исправляйте ZIP вручную и не отключайте проверки.
4. Импортируйте с режимом `rename` или `skip` согласно плану миграции.
5. Заново введите Bot token/MTProto credentials, проверьте identity и завершите авторизацию.
6. Для каждой группы заново подтвердите правила, permission evidence и Telegram validation.
7. Активируйте templates/flows/integrations вручную после ревизии.
8. Кампании остаются draft: выполните новый preview, preflight, approval, readiness и пилот.
9. Не переносите `.env`, базу, StringSession или секреты внутри configuration bundle.

### Удаление переносимого архива

После использования нажмите «Удалить». Проверьте audit event `configuration_bundle.deleted`. Удаление записи без удаления storage object считается инцидентом хранения; endpoint 1.6 выполняет оба действия одной операционной командой.

## 25. Подписи и доверие 1.7

### Проверка входящего configuration bundle

1. Получить ZIP и public fingerprint разными каналами.
2. Сверить fingerprint с владельцем исходной инсталляции.
3. Импортировать public key как trusted, но не как default.
4. Запустить автономный verifier.
5. Загрузить файл в «Подписи и доверие» и сверить `valid_trusted`.
6. Выполнить configuration preview.
7. Проверить предупреждения и только затем выполнить fail-safe import.

### Ошибка `valid_untrusted`

- подпись математически корректна;
- signer не доверен текущей организации;
- не включать trust только на основании embedded public key;
- запросить PEM/fingerprint по независимому каналу.

### Ошибка `revoked`

- не импортировать файл;
- определить, был ли он сформирован до или после отзыва;
- для production запросить новый артефакт, подписанный действующим ключом;
- зарегистрировать incident/операторскую заметку.

### Ошибка `invalid`

- считать файл повреждённым или подменённым;
- не пытаться вручную исправлять manifest;
- сохранить SHA-256 полученного файла;
- запросить повторную передачу;
- при повторении проверить канал передачи и audit исходной системы.

### Потеря default private key

Если запись активна, но private ciphertext не расшифровывается:

1. остановить формирование переносимых артефактов;
2. проверить master key и dry-run rotation;
3. не пытаться восстанавливать private key из public PEM;
4. при невозможности восстановления отозвать запись и создать новый key;
5. распространить новый fingerprint.

## 26. Recovery Assurance 1.8

Ежедневная операционная последовательность:

1. Создать backup CLI.
2. Сразу выполнить verify.
3. Убедиться, что evidence стал `verified`.
4. Проверить RPO dashboard.
5. По календарю выполнить restore drill.
6. Убедиться, что duration укладывается в RTO.
7. Скопировать archive/receipt/drill receipt off-site.
8. Проверить retained verified count перед удалением старых копий.

При ошибке verify не повторяйте статус вручную: сохраните failed evidence, создайте новый backup и расследуйте storage/keys/filesystem. При провале drill оставьте production publishing paused, если recovery policy является blocker commissioning.

`./scripts/restore.sh artifact receipt` выполняет только drill. Для полного disaster recovery используйте [BACKUP_RESTORE.md](BACKUP_RESTORE.md) и отдельную disposable environment.

## Supply Chain Assurance 2.1

### Подготовка upgrade evidence

1. В «Доверие к релизам» создайте/import attestation целевой версии и проверьте `valid_trusted`.
2. В «Поставка и зависимости» скачайте CycloneDX SBOM.
3. Передайте SBOM доверенному CI/scanner; получите report, содержащий hashes attestation и SBOM.
4. Импортируйте report, подпишите доверенным ключом и проверьте отсутствие blockers.
5. Опубликуйте attestation в transparency log.
6. Запустите проверку цепочки и сохраните last sequence/hash в change evidence.
7. Создайте change request с точными attestation и assessment IDs.
8. После любого изменения dependency policy создайте новый assessment; старый не переиспользуйте.

### Отзыв release

1. Остановите upgrade/change, если он ещё не начат.
2. Укажите причину и создайте `withdrawn` event.
3. Проверьте transparency chain.
4. Отзовите/замените release attestation и scanner evidence при компрометации ключа или сборки.
5. Создайте операторское уведомление и incident record во внешней системе, если release уже был развёрнут.

### Ошибка chain verification

Не добавляйте новые entries и не выполняйте upgrade. Экспортируйте DB/audit evidence, определите первую повреждённую sequence и восстановите систему из проверенного backup либо выполните документированное incident recovery. Не «исправляйте» hashes вручную.

## Operational SLO и инциденты

### Blocked assessment

1. Оставьте публикации заблокированными; не отключайте gate ради продолжения очереди.
2. Откройте раздел «Надёжность и инциденты» и найдите blocked check.
3. Подтвердите автоматически созданный критический инцидент.
4. Переведите его в `mitigating`, зафиксировав фактический план.
5. Для worker/queue проверьте service, heartbeat, leases, Redis/DB и журналы.
6. Для `waiting_review` вручную сверьте целевой Telegram-чат; не используйте автоматический retry.
7. Для error budget проанализируйте коды Telegram и остановите дальнейший staged rollout.
8. После устранения создайте новую оценку.
9. Убедитесь, что gate пройден и инцидент разрешён.
10. Возобновляйте кампанию только через обычный approval/preflight/readiness путь.

### Команды

```bash
python -m app.worker --once
curl -fsS https://panel.example.com/metrics | grep teleflow_slo
```

API оценки требует аутентификацию панели и CSRF; не размещайте административные cookies в shell history.

## Active/Standby и controlled failover 2.3

### Ежедневная проверка

1. Откройте раздел «Active / Standby».
2. Проверьте active site, epoch и holder worker.
3. Убедитесь, что lease не просрочен.
4. Проверьте offline sites и open failovers.
5. Сверьте attempts `network_started/uncertain`.

### Плановое переключение

1. Убедитесь, что target online и имеет ту же версию.
2. Остановите новые кампании или дождитесь завершения текущего пакета.
3. Создайте failover request с причиной.
4. Проверьте blockers.
5. Другой Owner/Admin вводит `ПЕРЕКЛЮЧИТЬ НА <site_key>`.
6. Убедитесь, что epoch увеличился.
7. Выполните одну служебную canary.
8. Не увеличивайте масштаб до проверки message ID.

### Неопределённая доставка

- не нажимайте обычный retry;
- найдите сообщение в фактическом Telegram-чате;
- выберите `confirmed_sent`, `confirmed_not_sent` или `skipped`;
- добавьте комментарий и message ID, если сообщение найдено;
- только после сверки разрешайте следующий staged batch.

## Continuity drill

### Simulation

1. Откройте «Непрерывность».
2. Проверьте active/standby runtime compatibility.
3. Создайте simulation и запустите её.
4. Убедитесь, что evidence показывает `network_calls=0`.
5. Передайте результат другому Owner/Admin для sign-off.

### Live failover/failback

1. Объявите maintenance window и проверьте отсутствие processing/uncertain jobs.
2. Создайте live-drill на выбранную standby.
3. Запустите drill; это создаёт обычный controlled failover request.
4. Другой Owner/Admin подтверждает failover точной фразой.
5. После подтверждения target active запросите failback.
6. Другой Owner/Admin подтверждает возврат на source.
7. Проверьте RTO, epochs, evidence SHA-256 и event chain.
8. Другой Owner/Admin принимает либо отклоняет evidence.

### Ошибка

Не выполняйте ручное изменение lease или epoch. При несовпадении runtime остановите drill, восстановите одинаковый release/config и создайте новое учение. Если target уже active, сначала выполните controlled failback.

## Capacity & Backpressure runbook

### Очередь заблокирована admission gate

1. Откройте «Нагрузка и лимиты».
2. Проверьте blockers: active, ready, runs, run size, utilization или drain time.
3. Не увеличивайте лимиты до выяснения причины.
4. Убедитесь, что worker heartbeat и execution lease актуальны.
5. Проверьте failed/waiting_review jobs и открытые инциденты.
6. Уменьшите размер кампании или используйте staged rollout.
7. После стабилизации создайте новый assessment.

### Сработал minute/hour dispatch budget

1. Telegram gateway уже не был создан; job должен остаться `pending`.
2. Проверьте `DeliveryAttempt`: для final race attempt будет `abandoned` и без `network_started_at`.
3. Не выполняйте ручной обход через другой аккаунт или endpoint.
4. Дождитесь освобождения rate-window или уменьшите плановый поток.

### Высокий estimated drain time

- остановите admission новых крупных runs;
- проверьте connection intervals и blackout/slow mode;
- разделите маршрут на пакеты;
- увеличивайте worker capacity только после проверки PostgreSQL и Telegram constraints;
- зафиксируйте изменение через Change Management.

### После изменения policy

Старый assessment становится stale. Создайте новый assessment и повторите canary до возобновления крупных кампаний.
