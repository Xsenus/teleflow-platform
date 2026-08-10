# Incident response

## 1. Приоритеты

1. Нажать organization-wide emergency stop и остановить дальнейшее воздействие.
2. Сохранить evidence без раскрытия secrets.
3. Определить затронутую организацию/connection/jobs/data.
4. Отозвать credentials при необходимости.
5. Восстановить безопасную работу.
6. Зафиксировать root cause и preventive action.

## 2. Telegram restriction

- включить emergency stop организации, если затронут маршрут/аккаунт неясен;
- pause connection/campaign;
- не переключаться на другой account/proxy для обхода;
- сохранить error/wait/job IDs;
- проверить конкретные группы и жалобы;
- проверить официальный status account;
- удалить/исправить запрещённые destinations;
- resume только после manual acknowledgement.

## 3. Ambiguous delivery

- job остаётся `waiting_review`;
- проверить Telegram chat вручную;
- если message найден — не retry;
- если точно отсутствует — retry после проверки limits;
- если неизвестно — cancel и уведомить оператора;
- зафиксировать возможный duplicate risk.

## 4. Bot token leak

1. Pause/revoke TeleFlow connection.
2. Revoke token у BotFather.
3. Проверить webhook/updates/unknown actions.
4. Создать новый token.
5. Подключить как новый secret.
6. Проверить destinations и audit.
7. Ротировать integration secrets, если они могли быть доступны вместе.

## 5. MTProto session leak

1. Pause connection.
2. Завершить active session в Telegram settings.
3. Revoke TeleFlow connection.
4. Проверить отправленные сообщения/входы.
5. Сменить Telegram 2FA password при риске.
6. Авторизовать новую StringSession.
7. Проверить host compromise.

## 6. Master/JWT secret leak

### Master key

Считать раскрытыми encrypted DB/storage fields из доступных копий. Изолировать host/DB/backup, включить emergency stop, отозвать Telegram/AI/Google credentials и уведомить ответственных. На чистом окружении восстановить проверенный backup, выполнить `rotate_master_key.py --dry-run`, затем controlled rotation с новым key. Не заменять environment key без re-encryption.

### JWT secret

- заменить secret;
- все access tokens станут недействительны;
- удалить/отозвать refresh token records;
- потребовать повторный login;
- проверить account changes.

## 7. PII leak

- определить subject/fields/time/recipient;
- отключить виновный endpoint/API key/user;
- сохранить audit/request IDs;
- выполнить юридически требуемые уведомления;
- провести privacy export/delete по запросу;
- проверить backups/provider logs;
- сократить scope/retention.

## 8. Outbox duplicate/loss

- pause affected integration;
- проверить event ID на remote side;
- сравнить per-endpoint delivery state;
- не выполнять retry до idempotency check;
- восстановить event из DB/audit при необходимости;
- добавить consumer deduplication.

## 9. Worker/API outage

- проверить health/container/systemd logs;
- проверить DB/Redis/disk/certificate;
- не запускать второй MTProto worker без остановки первого;
- после восстановления проверить stale leases/backlogs;
- просмотреть `waiting_review` до массового retry.

## 10. Evidence checklist

Сохранять без secrets:

```text
incident time/timezone
organization ID
connection/campaign/run/job/update/outbox IDs
request IDs
error codes and Telegram wait
redacted logs
configuration version
archive SHA/commit
operator actions
recovery verification
```

Не прикладывать `.env`, token, session string, auth code, full message body или candidate contacts в общий incident ticket.


## 11. Audit chain verification failure

Признаки: `GET /audit/verify` или CLI возвращает `valid=false`, missing state, sequence/hash mismatch.

1. Включить organization emergency stop; при system-chain инциденте остановить весь stack.
2. Не запускать `backfill`: он предназначен для legacy rows, а не для маскировки нарушения.
3. Остановить API/worker и сохранить read-only forensic copy БД/storage/logs.
4. Записать first error entry ID, sequence, expected/computed/stored hashes.
5. Сравнить с последним encrypted backup и внешним log sink.
6. Определить, имел ли субъект DB/admin access и мог ли переписать chain целиком.
7. При невозможности доказать целостность восстановить доверенную копию и ротировать credentials.
8. После восстановления выполнить verify, fake smoke и только затем resume.

Hash-chain обнаруживает локальные несогласованные изменения, но привилегированный DB-администратор способен переписать и записи, и head. Для high-assurance deployment нужен независимый WORM/SIEM export.

## 12. Ошибка или прерывание master-key rotation

1. Не запускать сервисы с новым или старым key наугад.
2. Сохранить stdout/stderr CLI без секретов и определить, завершилась ли DB transaction.
3. Проверить возможность `doctor`/dry-run старым и новым key на изолированной копии.
4. При неясном состоянии восстановить pre-rotation DB/storage backup.
5. Проверить encrypted privacy objects в S3/local storage по журналу операции.
6. Повторить dry-run и rotation только после root-cause анализа.
7. После успешной rotation обновить secret manager, verify audit, выполнить login/fake campaign.

## 13. Неожиданный approval/run

1. Emergency stop организации.
2. Зафиксировать campaign ID, approval request, decisions, fingerprint и actor sessions.
3. Отозвать подозрительные browser sessions/API keys.
4. Проверить route/template/destination permission и audit chain.
5. Отменить кампанию и pending jobs.
6. При компрометации owner/admin сменить пароль, TOTP и JWT/session secrets по риску.
7. Создать новую кампанию/approval только после расследования.

## 14. Истёкшее или отозванное разрешение группы

1. Немедленно отключите destination и поставьте organization/campaign на паузу при наличии активного run.
2. Не меняйте дату окончания без повторного доказуемого согласования.
3. Зафиксируйте источник отзыва, затронутые campaign/run/job и последние message IDs.
4. При необходимости удалите опубликованное сообщение только в пределах прав и согласованной процедуры.
5. Создайте новое approval только после повторной проверки правил.

## 15. Ошибка staged batch

1. Не подтверждайте checkpoint.
2. Разберите failed/skipped/waiting-review и вычислите фактическую долю проблем.
3. При признаках Telegram restriction примените глобальный emergency stop.
4. Выполните controlled abort, если причина не локализована.
5. Сохраните preflight report, run ID, batch number, audit/notification evidence.

## 16. Ошибка reconciliation

Если оператор ошибочно отметил неоднозначную доставку:

1. остановите связанные кампании и duplicate follow-up;
2. проверьте Telegram message history и audit trail;
3. не редактируйте старую review-запись в БД и не пытайтесь скрыть её cancel/revoke; оформите корректирующее audit event/incident;
4. при созданном дубле согласуйте его удаление с администратором группы;
5. пересмотрите права reviewer и операционную инструкцию.

## 12. Pilot-stage containment 1.5

При инциденте публикаций порядок containment следующий:

```text
emergency stop
→ freeze/resolve uncertain deliveries
→ lower pilot stage
→ verify incompatible jobs are paused
→ revoke compromised connection/session when required
→ create redacted support bundle
→ verify audit hash-chain
```

Понижение pilot stage не заменяет emergency stop: первое ограничивает будущий масштаб и приостанавливает несовместимые маршруты, второе немедленно блокирует всю организацию. Не повышать этап обратно до установления причины, свежей Telegram validation, реальной canary и нового assessment.

Support bundle допустим для первичной диагностики, но не является forensic image. Для юридически значимого расследования отдельно сохраняются проверенные DB/storage backups и внешние immutable logs в соответствии с политикой организации.

## 17. Компрометация signing key 1.7

1. При влиянии на рабочую инсталляцию включить emergency stop.
2. Зафиксировать key ID, fingerprint, время, известные signed artifact SHA-256 и audit head.
3. Отозвать key. Приватный ciphertext будет уничтожен; public metadata останется.
4. На принимающих инсталляциях снять trust или отозвать matching public key.
5. Проверить последние configuration/support/acceptance artifacts автономным verifier.
6. Создать новый local key и выполнить независимую сверку fingerprint.
7. Переформировать критичные артефакты новым signer.
8. При подозрении на компрометацию master key ротировать master key и все Telegram/API credentials согласно риску.
9. Не считать `created_at` доказательством внешнего времени: это подписанное системное время, а не TSA.

## 18. Недействительная подпись входящего артефакта

1. Не импортировать и не перепаковывать файл.
2. Сохранить внешний SHA-256 и канал получения.
3. Зафиксировать verifier error: payload hash, manifest composition, purpose, metadata или Ed25519 failure.
4. Сверить public fingerprint по независимому каналу.
5. Запросить новый артефакт у источника.
6. При повторной ошибке считать канал или исходную инсталляцию потенциально скомпрометированными.

## 19. Operational SLO и error-budget incident 2.2

### Срабатывание

Инцидент создаётся автоматически только для `blocked` SLO check и получает стабильный `dedup_key=slo:<check_code>`. Повторная оценка обновляет ссылку на evidence вместо создания копий.

### Первые действия

1. Сохранить ID assessment и его fingerprint.
2. Проверить, актуальны ли policy SHA-256 и `expires_at`.
3. Подтвердить инцидент и назначить владельца.
4. Не запускать публикации через альтернативный endpoint: API, scheduler и Safety Engine используют один gate.
5. Не выполнять retry неопределённой Telegram-доставки до ручной сверки.
6. Зафиксировать влияние на кампании, группы и change window.

### Восстановление

- устранить первичную причину;
- выполнить новую SLO-оценку;
- проверить отсутствие blockers и допустимый error budget;
- убедиться, что автоматический SLO-инцидент перешёл в `resolved` либо разрешить ручной инцидент с root cause;
- проверить Prometheus/Grafana и очередь;
- возобновлять rollout только по staged checkpoint.

### Evidence

К incident record прикладываются ID assessment, policy hash, fingerprint, error codes, operator events, времена acknowledge/mitigate/resolve, root cause и postmortem URL. Системный источник инцидента нельзя задавать через публичный ручной API.

## 20. Split-brain, stale lease и failover incident 2.3

### Признаки

- `TeleFlowExecutionLeaseStale`;
- два хоста заявляют active;
- open failover дольше согласованного окна;
- `WORKER_CRASH_DURING_SEND`;
- попытка `network_started` без результата;
- offline active site.

### Немедленные действия

1. Включите organization emergency stop.
2. Не запускайте Telegram retry и не меняйте credentials.
3. Остановите worker на подозреваемой старой площадке.
4. Проверьте authoritative PostgreSQL lease и epoch.
5. Сверьте все `network_started/uncertain` сообщения непосредственно в Telegram.
6. Проведите controlled failover только после очистки blockers.
7. Выполните одну служебную canary.

### Сбор evidence

Сохраните:

- execution overview;
- site heartbeat timestamps;
- lease holder и epoch;
- failover request/blockers;
- delivery attempt ledger;
- worker logs и request IDs;
- Telegram message IDs;
- audit-chain verification;
- incident timeline.

### Запрещено

- запускать второй writable PostgreSQL primary;
- вручную уменьшать epoch;
- удалять uncertain attempt;
- продолжать через другой аккаунт после anti-spam restriction;
- использовать failover для обхода FloodWait.

## Continuity incident

При зависшем или повреждённом continuity-drill:

1. Зафиксируйте текущие `active_site_key`, holder, epoch и open failover requests.
2. Остановите новые публикации организации.
3. Не выполняйте автоматический retry и не редактируйте lease вручную.
4. Проверьте event chain через `/api/v1/continuity/drills/{id}/verify`.
5. Если standby active, выполните controlled failback с независимым подтверждением.
6. Экспортируйте support bundle и audit evidence.
7. Повреждённое evidence отклоните; после восстановления создайте новое учение.
8. Обновите incident root cause и postmortem.

## Capacity incidents 2.5

### Признаки

- `TeleFlowCapacityBackpressureActive`;
- `TeleFlowCapacityAssessmentStale`;
- `TeleFlowCapacityDrainTimeHigh`;
- длительный рост ready/held jobs;
- повторяющиеся `CAPACITY_*` decisions;
- processing jobs не завершаются в ожидаемый срок.

### Первичные действия

1. Не отключайте обязательный gate.
2. При угрозе повторных публикаций используйте organization emergency stop.
3. Сохраните assessment, queue snapshot и relevant delivery attempts.
4. Проверьте worker, execution lease, SLO и Telegram restrictions.
5. Зафиксируйте инцидент и ответственного.

### Восстановление

- устраните зависшие jobs через штатную reconciliation/recovery;
- уменьшите размер пакета или admission limits;
- дождитесь освобождения rate-window;
- создайте новый assessment;
- выполните одну canary;
- возобновляйте staged rollout только после контрольного checkpoint.

Нельзя считать повышение лимитов устранением причины без load evidence и Telegram-canary.
