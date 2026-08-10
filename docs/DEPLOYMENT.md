# Развёртывание TeleFlow Platform 1.8

## 1. Production prerequisites

- Ubuntu 22.04/24.04 или аналогичный Linux;
- домен и TLS certificate;
- PostgreSQL 16+;
- Redis 7+;
- 2 vCPU и 4 GB RAM для небольшого пилота без локального antivirus;
- 20+ GB SSD плюс media/backup;
- outbound HTTPS к Telegram и выбранным providers;
- отдельный encrypted/off-site backup target;
- опциональный `clamd` при включённой проверке uploads.

Если ClamAV запускается на том же сервере, закладывайте дополнительную память под daemon и signature database.

## 2. Secret preparation

```bash
python scripts/generate_secrets.py
```

Сохраните значения в secret manager или root-only `.env`:

```bash
chmod 600 .env
```

Никогда не коммитьте `.env`, Telegram session, Bot token, service-account JSON, age identity или резервную копию БД.

## 3. Docker deployment

```bash
git clone <private repository> /opt/teleflow-platform
cd /opt/teleflow-platform
cp deploy/.env.production.example .env
nano .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Проверка:

```bash
docker compose ps
docker compose logs migrate
docker compose exec api python scripts/doctor.py
curl --fail http://127.0.0.1:8080/api/v1/health/live
```

`migrate` является one-shot service и должен успешно завершиться до запуска API/worker.

## 4. Обновление 1.6.0 → 1.7.0

### Обязательный порядок

1. Включить organization emergency stop и остановить API/worker.
2. Создать backup БД и storage; выполнить проверку восстановления на отдельном пути.
3. Сохранить текущий master key отдельно от backup.
4. Обновить исходный код и зависимости.
5. Установить production policy:

```text
TELEFLOW_ARTIFACT_SIGNATURE_POLICY=require_trusted
```

6. Выполнить migration:

```bash
alembic upgrade head
alembic current
alembic check
```

Ожидаемый head:

```text
3b7d9f1a2c4e
```

7. Запустить API без снятия emergency stop.
8. В разделе «Подписи и доверие» создать локальный Ed25519-ключ:
   - `make_default=true`;
   - `trusted_for_import=true`;
   - зафиксировать public fingerprint в защищённом реестре.
9. Экспортировать публичный PEM и проверить его автономно на административной машине.
10. Создать тестовый configuration bundle и выполнить:

```bash
python scripts/verify_artifact.py test-bundle.zip \
  --trusted-public-key teleflow-public.pem \
  --require-trusted \
  --json
```

11. Запустить commissioning. Check `artifact_signing` должен пройти.
12. Запустить worker, проверить `/health/ready`, audit-chain и создание signed support bundle.
13. Только после этого снять emergency stop.

### Legacy unsigned bundles

Production `require_trusted` не принимает unsigned bundle. Для переноса старого архива безопаснее:

1. проверить старый файл на изолированной 1.6/1.7 development-инсталляции;
2. выполнить fail-safe import;
3. сформировать новый signed bundle;
4. импортировать его в production после проверки fingerprint.

Не следует временно ослаблять production policy ради одного legacy-файла.

### Rollback 1.7 → 1.6

```bash
alembic downgrade 2a6f0104062a
```

Rollback удаляет registry signing keys и signature metadata из БД. ZIP/JSON-файлы в storage могут оставаться подписанными, но 1.6 не использует trust policy. До rollback обязательно сохранить:

- backup БД;
- публичные PEM и fingerprints;
- список отозванных ключей;
- signed artifacts, необходимые для расследования.

После возврата на 1.6 не создавайте новые переносимые артефакты для production-передачи, пока 1.7 не восстановлен.

## 5. Обновление 1.5.0 → 1.6.0

Release 1.6 добавляет commissioning reports, формальные pilot programs и безопасные configuration bundles. Обновление выполняется с остановленными writers и проверенным backup.

### Обязательный порядок

1. Включите organization emergency stop.
2. Разрешите все `DELIVERY_RESULT_UNCERTAIN`; убедитесь, что нет jobs в `processing`.
3. Создайте encrypted backup БД/storage и проверьте checksum.
4. Остановите API и worker.
5. Разверните код/образ 1.6.0 и зависимости.
6. Добавьте `TELEFLOW_COMMISSIONING_TTL_MINUTES=30` и сверяйте `.env` с актуальным example.
7. Выполните `alembic upgrade head`; ожидаемый head — `2a6f0104062a`.
8. Выполните `alembic check`, `doctor`, audit-chain verify и master-key dry-run.
9. Запустите API/worker, не снимая emergency stop.
10. Откройте «Ввод в эксплуатацию» и создайте commissioning report.
11. Устраните blockers, затем выполните local fake stage формальной программы пилота.
12. Для переноса ПК → сервер используйте configuration bundle; Telegram credentials и разрешения подключите заново.

Новая настройка:

```env
TELEFLOW_COMMISSIONING_TTL_MINUTES=30
```

Bare metal verification:

```bash
sudo systemctl stop teleflow-worker teleflow-api
sudo -u teleflow /opt/teleflow-platform/scripts/backup.sh
sudo -u teleflow /opt/teleflow-platform/.venv/bin/alembic -c /opt/teleflow-platform/alembic.ini upgrade head
sudo -u teleflow /opt/teleflow-platform/.venv/bin/alembic -c /opt/teleflow-platform/alembic.ini check
sudo -u teleflow /opt/teleflow-platform/.venv/bin/python /opt/teleflow-platform/scripts/doctor.py --json
sudo -u teleflow /opt/teleflow-platform/.venv/bin/python /opt/teleflow-platform/scripts/audit_chain.py verify --include-system --json
sudo systemctl start teleflow-api teleflow-worker
```

### Migration compatibility и rollback

Release-QA проверяет downgrade к `e2f4a6c8d0b1` и повторный upgrade. После создания commissioning reports, pilot programs или configuration bundles предпочтителен rollback через pre-upgrade backup: версия 1.5 не понимает новые сущности и acceptance evidence.

### Историческое обновление 1.4.0 → 1.5.0

Для перехода со старой 1.4 сначала используйте процедуру и migration 1.5 из [RELEASE_NOTES_1.5.md](RELEASE_NOTES_1.5.md), затем примените шаги 1.5→1.6 выше.

## 6. Обновление 1.3.0 → 1.4.0

Release 1.4 добавляет destination validation history/TTL, operational blackout calendar и обязательный Production Pilot readiness. Обновление выполняется в maintenance window с остановленными writers и проверенным backup.

### Обязательный порядок

1. Включите organization emergency stop.
2. Убедитесь, что нет jobs в `processing`; разрешите все `DELIVERY_RESULT_UNCERTAIN`.
3. Создайте encrypted backup БД/storage и проверьте checksum.
4. Остановите API и worker.
5. Разверните код/образ 1.4.0 и установите зависимости.
6. Добавьте новые environment variables; в production оставьте readiness gate включённым.
7. Выполните `alembic upgrade head`; ожидаемый head — `c9e1f3a5b7d9`.
8. Выполните `alembic check`, `doctor`, audit-chain verify.
9. Запустите API/worker, не снимая emergency stop.
10. В разделе Production Pilot повторно проверьте все live destinations небольшими пакетами.
11. Создайте readiness report для тестовой кампании и выполните fake staged run.
12. Проверьте one-time и weekly blackout до gateway call.
13. Только после live-проверки одной служебной группы снимайте pause и переходите к пяти разрешённым группам.

Bare metal:

```bash
sudo systemctl stop teleflow-worker teleflow-api
sudo -u teleflow /opt/teleflow-platform/scripts/backup.sh
sudo -u teleflow /opt/teleflow-platform/.venv/bin/pip install -r /opt/teleflow-platform/requirements.txt
sudo -u teleflow /opt/teleflow-platform/.venv/bin/alembic -c /opt/teleflow-platform/alembic.ini upgrade head
sudo -u teleflow /opt/teleflow-platform/.venv/bin/alembic -c /opt/teleflow-platform/alembic.ini check
sudo -u teleflow /opt/teleflow-platform/.venv/bin/python /opt/teleflow-platform/scripts/doctor.py --json
sudo -u teleflow /opt/teleflow-platform/.venv/bin/python /opt/teleflow-platform/scripts/audit_chain.py verify --include-system --json
sudo systemctl start teleflow-api teleflow-worker
```

Docker Compose:

```bash
cd /opt/teleflow-platform
./scripts/backup.sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop api worker
docker compose -f docker-compose.yml -f docker-compose.prod.yml build
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm api alembic check
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm api python scripts/doctor.py --json
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Новые настройки:

```env
TELEFLOW_DESTINATION_VALIDATION_TTL_HOURS=168
TELEFLOW_CONNECTION_HEALTH_TTL_HOURS=24
TELEFLOW_READINESS_TTL_MINUTES=30
TELEFLOW_PILOT_READINESS_REQUIRED=true
TELEFLOW_PILOT_STAGED_THRESHOLD=5
```

### Данные legacy destinations

Migration ставит `validated_at=updated_at` для прежних `validated=true` строк. Это только совместимость схемы: перед live-публикацией выполните фактическую revalidation через выбранный Bot API/MTProto gateway.

### Rollback 1.4 → 1.3

Release-QA проверяет технический downgrade к `b7c9d2e4f6a8`. Если после обновления уже созданы validation records, blackout rules или readiness reports, production rollback выполняйте через pre-upgrade backup. Worker 1.3 не понимает readiness gate и не должен запускаться поверх схемы 1.4.

## 7. Историческое обновление 1.2.0 → 1.3.0

Release 1.3 добавляет persisted preflight, сроки разрешений, staged rollout и reconciliation. Обновление выполняется в maintenance window с остановленными API/worker и проверенным backup.

### Обязательный порядок

1. Поставьте organization publishing на аварийную паузу и убедитесь, что нет job в `processing`.
2. Разрешите или явно закройте все `DELIVERY_RESULT_UNCERTAIN`; не переносите неоднозначные результаты в новую версию без операционного решения.
3. Создайте зашифрованный backup БД и storage, проверьте checksum и тестовое чтение.
4. Остановите API и worker.
5. Разверните код/образ 1.3.0 и зависимости.
6. Выполните `alembic upgrade head`; ожидаемый head — `b7c9d2e4f6a8`.
7. Выполните `alembic check`, `scripts/doctor.py` и audit-chain verify.
8. Запустите API/worker, не снимая organization pause.
9. Для каждого активного destination заполните/проверьте `permission_expires_at`; для разрешений без формального срока используйте дату следующей обязательной ревизии.
10. Выполните fake staged campaign: batch 1 → manual checkpoint → batch 2 → controlled abort.
11. Проверьте reconciliation на тестовом ambiguous job без Telegram network call.
12. Только после smoke снимайте organization pause и проходите live rollout 1 → 5 → 20.

Bare metal:

```bash
sudo systemctl stop teleflow-worker teleflow-api
sudo -u teleflow /opt/teleflow-platform/scripts/backup.sh
sudo -u teleflow /opt/teleflow-platform/.venv/bin/pip install -r /opt/teleflow-platform/requirements.txt
sudo -u teleflow /opt/teleflow-platform/.venv/bin/alembic -c /opt/teleflow-platform/alembic.ini upgrade head
sudo -u teleflow /opt/teleflow-platform/.venv/bin/alembic -c /opt/teleflow-platform/alembic.ini check
sudo -u teleflow /opt/teleflow-platform/.venv/bin/python /opt/teleflow-platform/scripts/doctor.py --json
sudo -u teleflow /opt/teleflow-platform/.venv/bin/python /opt/teleflow-platform/scripts/audit_chain.py verify --include-system --json
sudo systemctl start teleflow-api teleflow-worker
```

Docker Compose:

```bash
cd /opt/teleflow-platform
./scripts/backup.sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop api worker
docker compose -f docker-compose.yml -f docker-compose.prod.yml build
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm api alembic check
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm api python scripts/doctor.py --json
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Новые необязательные настройки:

```env
TELEFLOW_PREFLIGHT_TTL_MINUTES=60
TELEFLOW_PERMISSION_EXPIRY_WARNING_DAYS=14
```

### Rollback 1.3 → 1.2

Release-QA проверяет технический downgrade к `f4a6b8c12d34`. После появления preflight reports, held jobs, batch state или reconciliation decisions production rollback выполняйте через pre-upgrade backup. Старый worker не понимает `held`/`awaiting_checkpoint` и не должен запускаться поверх схемы 1.3.

## 8. Историческое обновление 1.1.0 → 1.2.0

Обновление governance-релиза выполняется при остановленных writers, потому что после migration нужно связать legacy audit rows в последовательную chain.

### Обязательный порядок

1. Убедитесь, что очередь не выполняет критическую live-операцию.
2. Создайте encrypted backup БД и storage.
3. Проверьте checksum и возможность чтения backup.
4. Остановите API и worker.
5. Разверните код/образ 1.2.0 и установите зависимости.
6. Выполните `alembic upgrade head`.
7. Выполните offline audit backfill и verify.
8. Запустите API/worker.
9. Проверьте dashboard, notifications, governance settings и fake campaign.
10. Только после smoke возобновите live Telegram.

Bare metal:

```bash
sudo systemctl stop teleflow-worker teleflow-api
sudo -u teleflow /opt/teleflow-platform/scripts/backup.sh
sudo -u teleflow /opt/teleflow-platform/.venv/bin/pip install -r /opt/teleflow-platform/requirements.txt
sudo -u teleflow /opt/teleflow-platform/.venv/bin/alembic -c /opt/teleflow-platform/alembic.ini upgrade head
sudo -u teleflow /opt/teleflow-platform/.venv/bin/python /opt/teleflow-platform/scripts/audit_chain.py backfill --include-system --yes
sudo -u teleflow /opt/teleflow-platform/.venv/bin/python /opt/teleflow-platform/scripts/audit_chain.py verify --include-system --json
sudo systemctl start teleflow-api teleflow-worker
```

Docker Compose:

```bash
cd /opt/teleflow-platform
./scripts/backup.sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop api worker
docker compose -f docker-compose.yml -f docker-compose.prod.yml build
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm api \
  python scripts/audit_chain.py backfill --include-system --yes
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm api \
  python scripts/audit_chain.py verify --include-system --json
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Migration head `f4a6b8c12d34` добавляет approval requests/decisions, organization stop state, notifications и audit chain state. Legacy audit rows не переписываются самой Alembic migration, чтобы online DDL не смешивался с долгой data migration.

### Rollback 1.2 → 1.1

Release-QA проверяет технический downgrade к `d8b4a210f6c3`, но production rollback должен выполняться через восстановление pre-upgrade backup, если в 1.2 уже появились approval/notification/audit-chain данные. Не запускайте старый worker поверх новой схемы и не удаляйте backup до успешного smoke/restore drill.

### Master-key rotation после обновления

Ротация не является обязательной частью migration. Выполняйте её отдельным maintenance window:

```bash
# сервисы остановлены, backup проверен
TELEFLOW_NEW_MASTER_KEY_FILE=/secure/new-master-key \
  python scripts/rotate_master_key.py --dry-run
TELEFLOW_NEW_MASTER_KEY_FILE=/secure/new-master-key \
  python scripts/rotate_master_key.py --yes
```

После успешной операции замените `TELEFLOW_MASTER_KEY` в secret manager на новый key **до** запуска API/worker. Старый ключ храните только в изолированном rollback escrow до завершения restore verification, затем уничтожьте согласно policy.

## 9. Host Nginx и TLS

Скопируйте и измените:

```text
deploy/nginx/teleflow-https.example.conf
```

Замените domain/certificate paths. Internal compose Nginx слушает loopback. Проверка:

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -I https://panel.example.com/
```

Telegram Business webhook требует публичный HTTPS URL. `/metrics` должен оставаться недоступным извне.

## 10. Bare-metal/systemd

```bash
sudo useradd --system --home /opt/teleflow-platform --shell /usr/sbin/nologin teleflow
sudo mkdir -p /opt/teleflow-platform /opt/teleflow-platform/{data,storage}
sudo chown -R teleflow:teleflow /opt/teleflow-platform
```

Создайте venv и установите dependencies:

```bash
sudo -u teleflow python3.12 -m venv /opt/teleflow-platform/.venv
sudo -u teleflow /opt/teleflow-platform/.venv/bin/pip install -r /opt/teleflow-platform/requirements.txt
```

Environment:

```bash
sudo install -m 600 -o root -g teleflow deploy/.env.production.example /etc/teleflow-platform.env
```

Units:

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now teleflow-migrate teleflow-api teleflow-worker
```

## 11. Storage

### Local

- bind volume `/app/storage`;
- filesystem permissions 0700/0770;
- backup вместе с DB;
- не использовать ephemeral container layer.

### S3-compatible

Задайте endpoint, region, bucket, access/secret. Bucket должен быть private. Рекомендуются versioning, lifecycle, server-side encryption и IAM policy только на нужный prefix.

## 12. ClamAV

TeleFlow не запускает `clamscan` через shell. Он подключается к `clamd` по TCP и отправляет bytes командой INSTREAM.

Environment:

```env
TELEFLOW_ANTIVIRUS_MODE=clamav
TELEFLOW_ANTIVIRUS_FAIL_CLOSED=true
TELEFLOW_CLAMAV_HOST=clamav
TELEFLOW_CLAMAV_PORT=3310
TELEFLOW_CLAMAV_TIMEOUT_SECONDS=8
```

Проверьте, что `clamd` доступен только из внутренней сети приложения. Не публикуйте port 3310 в Интернет.

Production policy:

- public/third-party uploads: `FAIL_CLOSED=true`;
- scanner outage: upload возвращает 503;
- malware result: upload отклоняется до storage и создаёт critical audit event;
- `FAIL_CLOSED=false` допускается только после документированного risk acceptance.

## 13. Redis locks

```env
TELEFLOW_USE_REDIS_LOCKS=true
TELEFLOW_REDIS_URL=redis://:strong-password@redis:6379/0
```

Locks защищают singleton schedule/retention. Delivery correctness также обеспечивается DB leases.

## 14. PWA

PWA не требует отдельного сервера: manifest, icons, service worker и offline page обслуживает FastAPI static layer.

Условия установки:

- HTTPS или localhost;
- корректный `manifest.webmanifest`;
- service worker доступен по `/sw.js`;
- static assets не блокируются CSP/Nginx.

После обновления новый cache name активируется при следующей загрузке. Service worker не кэширует API, webhook, metrics и dynamic responses. Для принудительного обновления закройте все вкладки панели или удалите старый service worker в browser DevTools.

## 15. Readiness

```env
TELEFLOW_REQUIRE_WORKER_FOR_READINESS=true
TELEFLOW_WORKER_READINESS_MAX_AGE_SECONDS=60
TELEFLOW_PILOT_READINESS_REQUIRED=true
TELEFLOW_READINESS_TTL_MINUTES=30
TELEFLOW_DESTINATION_VALIDATION_TTL_HOURS=168
TELEFLOW_CONNECTION_HEALTH_TTL_HOURS=24
```

Load balancer использует `/api/v1/health/ready`, process monitor — `/api/v1/health/live`.

## 16. Post-deploy

1. Смените bootstrap password.
2. Включите TOTP для owner/admin.
3. Создайте второго emergency owner.
4. Настройте four-eyes/high-risk policy так, чтобы её можно было выполнить текущим числом администраторов.
5. Проверьте organization emergency stop и resume в fake mode.
6. Выполните audit backfill/verify после upgrade.
7. Проверьте список активных sessions и отзовите лишние.
8. Выполните fake smoke с явным approval request.
9. Проверьте центр уведомлений и acknowledgement critical event.
10. Проверьте PWA no-sensitive-cache policy.
11. При включённом ClamAV проверьте clean file, EICAR в закрытом тестовом окружении и scanner outage policy.
12. Настройте backup/alerts.
13. Выполните restore drill.
14. Повторно проверьте destinations и убедитесь, что validation TTL отображается корректно.
15. Создайте one-time и weekly blackout smoke rules.
16. Создайте свежий Production Pilot readiness report без blockers.
17. Только затем подключайте live Telegram и проходите пилот 1 → 5 → 20 → 50 → 100.

## 5. Обновление 1.7.0 → 1.8.0

1. Включить emergency stop, остановить API/worker.
2. Сохранить существующую copy БД/storage и keys escrow.
3. Обновить код и зависимости.
4. Настроить recovery environment variables.
5. Выполнить `alembic upgrade head`, `alembic check`, doctor и audit verify.
6. Запустить API с сохранённым emergency stop.
7. Создать 1.8 backup, выполнить verify и restore drill.
8. Проверить commissioning `recovery_assurance`.
9. Запустить worker и продолжить staged pilot.

Migration head: `4c8e0a2b6d1f`. Downgrade target: `3b7d9f1a2c4e`.

Production дополнительно требует:

```env
TELEFLOW_RECOVERY_REQUIRE_ENCRYPTED_BACKUP=true
TELEFLOW_RECOVERY_REQUIRE_TRUSTED_SIGNATURE=true
TELEFLOW_RECOVERY_AGE_RECIPIENT=age1...
TELEFLOW_ARTIFACT_SIGNATURE_POLICY=require_trusted
```

### Rollback 1.8 → 1.7

Перед downgrade экспортируйте/сохраните recovery receipts и drill evidence. Downgrade удаляет recovery metadata tables, но не должен удалять физические backup archives. Версия 1.7 не понимает compliance 1.8 и не должна использоваться для утверждения RPO/RTO.

## 17. Обновление 1.8.0 → 1.9.0

Рекомендуемый production-процесс теперь управляется Change Management:

1. Создать change request типа `upgrade`, указать текущую и целевую версии, риск и rollback plan.
2. Получить независимое одобрение другого Owner/Admin.
3. Включить maintenance mode через панель «Изменения». Он блокирует scheduler и Telegram Safety Engine до сетевого вызова.
4. Сделать и проверить Recovery Assurance backup.
5. Выполнить pre-change verification; blockers должны отсутствовать.
6. Остановить API/worker на время замены кода и миграции в соответствии с инфраструктурным runbook.
7. Обновить код и зависимости до 1.9.0.
8. Выполнить `alembic upgrade head` и `alembic check`.
9. Запустить API/worker, но оставить maintenance mode включённым.
10. Выполнить post-change verification. Он проверяет migration state, target app version, audit-chain и worker heartbeat policy.
11. Только после успешной проверки отметить change как completed и снять maintenance mode.
12. Создать свежий Production Pilot readiness report перед возобновлением live-публикаций.

Migration head 1.9: `5d9f1b3c7e2a`. Downgrade target 1.8: `4c8e0a2b6d1f`.

### Rollback 1.9 → 1.8

Если upgrade признан failed, change request фиксируется с `rollback_required`. Оставьте maintenance mode включённым, восстановите совместимый код/конфигурацию, при необходимости выполните `alembic downgrade 4c8e0a2b6d1f`, затем повторите проверки. Не снимайте maintenance до подтверждения согласованной версии приложения и схемы.

## 18. Обновление 1.9.0 → 2.0.0

1. Создайте проверенную резервную копию и выполните restore drill согласно Recovery Assurance.
2. Создайте change request и включите maintenance mode.
3. Обновите исходники до 2.0.0 и зависимости.
4. Выполните `alembic upgrade head`; ожидаемый head — `6e0a2c4f8b1d`.
5. В production установите `TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION=true`.
6. Создайте или импортируйте trusted release attestation целевой сборки и привяжите его к upgrade change request.
7. Выполните post-change verification, HTTP smoke и worker check.
8. Только после успешной проверки завершите change request и maintenance mode.

Откат схемы 2.0 к 1.9 проверен командой `alembic downgrade 5d9f1b3c7e2a`.

## 19. Обновление 2.0.0 → 2.1.0

1. Создайте change request для 2.1 и независимое approval.
2. Включите maintenance mode и убедитесь, что Telegram Safety Engine блокирует network calls.
3. Создайте verified Recovery Assurance backup и актуальный restore drill.
4. Обновите код и установите dependency `packaging==25.0` из pinned `requirements.txt`.
5. Выполните `alembic upgrade head`; ожидаемый head — `7f1b3d5e9a2c`.
6. Выполните `alembic check`.
7. Установите production flags:

```env
TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION=true
TELEFLOW_REQUIRE_RELEASE_TRANSPARENCY=true
TELEFLOW_REQUIRE_DEPENDENCY_ASSESSMENT=true
TELEFLOW_DEPENDENCY_REQUIRE_VULNERABILITY_SCAN=true
TELEFLOW_DEPENDENCY_REQUIRE_TRUSTED_REPORT=true
TELEFLOW_DEPENDENCY_ASSESSMENT_TTL_HOURS=24
TELEFLOW_DEPENDENCY_REPORT_MAX_BYTES=5242880
```

8. Создайте trusted 2.1 release attestation и опубликуйте его в transparency log.
9. Скачайте SBOM, выполните vulnerability scanner, нормализуйте и подпишите report.
10. Создайте assessment без blockers и привяжите его к upgrade change request.
11. Выполните pre/post deployment verification, HTTP smoke, worker check и transparency verify.
12. Завершите change request и только затем снимите maintenance mode.

### Rollback 2.1 → 2.0

Оставьте maintenance mode включённым. Сохраните exported SBOM/reports/transparency evidence, восстановите код 2.0 и выполните:

```bash
alembic downgrade 6e0a2c4f8b1d
alembic check
```

2.0 не понимает dependency assessments и не должен использоваться для подтверждения supply-chain policy 2.1. После повторного upgrade создайте или перепроверьте assessment заново.

## 19. Обновление 2.0.0 → 2.1.0

1. Создайте verified backup и актуальный restore drill.
2. Создайте upgrade change request и включите maintenance mode.
3. Обновите код и Python dependencies до 2.1.0.
4. Выполните `alembic upgrade head`; ожидаемый head — `7f1b3d5e9a2c`.
5. В production установите:

```text
TELEFLOW_REQUIRE_RELEASE_TRANSPARENCY=true
TELEFLOW_REQUIRE_DEPENDENCY_ASSESSMENT=true
TELEFLOW_DEPENDENCY_REQUIRE_VULNERABILITY_SCAN=true
TELEFLOW_DEPENDENCY_REQUIRE_TRUSTED_REPORT=true
TELEFLOW_DEPENDENCY_ASSESSMENT_TTL_HOURS=24
TELEFLOW_DEPENDENCY_REPORT_MAX_BYTES=5242880
```

6. Создайте/import trusted release attestation 2.1.
7. Сформируйте SBOM, получите реальный vulnerability report и создайте dependency assessment.
8. Опубликуйте attestation в transparency log.
9. Привяжите точные attestation/assessment IDs к change request и выполните pre-change verification.
10. После deployment выполните post-change verification, HTTP smoke и worker once.
11. Завершите change request и снимите maintenance mode только при отсутствии blockers.

Проверенный downgrade target: `alembic downgrade 6e0a2c4f8b1d`. Перед downgrade убедитесь, что изменения 2.1 не используются активными change requests и сохраните экспорт evidence, если он нужен для расследований.

## 22. Обновление 2.1.0 → 2.2.0

1. Создайте подписанный backup и выполните restore verification.
2. Создайте change request с rollback plan и независимым approval.
3. Включите maintenance mode и остановите worker/API в соответствии с runbook.
4. Обновите код и зависимости до 2.2.0.
5. Добавьте переменные `TELEFLOW_SLO_*`; в production установите `TELEFLOW_SLO_GATE_REQUIRED=true`.
6. Выполните:

```bash
alembic upgrade head
alembic current
alembic check
```

Ожидаемый head:

```text
8a2c4e6f0b3d
```

7. Запустите API и worker, дождитесь свежего heartbeat.
8. В разделе «Надёжность и инциденты» создайте первую SLO-оценку.
9. Устраните blockers, выполните commissioning и post-change verification.
10. Только после этого отключите maintenance mode и продолжите staged pilot.

Downgrade schema:

```bash
alembic downgrade 7f1b3d5e9a2c
```

Перед downgrade экспортируйте историю SLO/инцидентов, если она нужна для расследования. Production rollback приложения должен выполняться вместе с согласованным schema rollback.

## 24. Обновление 2.2.0 → 2.3.0

### Перед обновлением

1. Остановите worker и scheduler на всех площадках.
2. Убедитесь, что нет jobs `processing` и нерешённых uncertain deliveries.
3. Выполните проверенный backup и isolated restore drill.
4. Зафиксируйте текущий Alembic head `8a2c4e6f0b3d`.

### Обновление схемы

```bash
alembic upgrade head
alembic current
```

Ожидаемый head:

```text
9b3d5f7a1c4e
```

### Primary

```env
TELEFLOW_EXECUTION_FENCING_REQUIRED=true
TELEFLOW_EXECUTION_SITE_KEY=primary
TELEFLOW_EXECUTION_SITE_NAME=Основная площадка
TELEFLOW_EXECUTION_PRIMARY_SITE_KEY=primary
TELEFLOW_EXECUTION_LEASE_TTL_SECONDS=45
TELEFLOW_EXECUTION_SITE_HEARTBEAT_TTL_SECONDS=90
TELEFLOW_EXECUTION_REQUIRE_DISTINCT_FAILOVER_APPROVER=true
```

### Standby

```env
TELEFLOW_EXECUTION_FENCING_REQUIRED=true
TELEFLOW_EXECUTION_SITE_KEY=standby-eu-2
TELEFLOW_EXECUTION_SITE_NAME=Резервная площадка EU-2
TELEFLOW_EXECUTION_PRIMARY_SITE_KEY=primary
```

Обе площадки используют одну PostgreSQL database URL и одинаковые master/JWT secrets, но разные site keys.

### Порядок запуска

1. Запустите migrate service один раз.
2. Запустите API на primary.
3. Запустите primary worker и дождитесь heartbeat.
4. Запустите standby API/worker.
5. Откройте «Active / Standby» и убедитесь, что primary active, standby online.
6. Выполните canary на primary.
7. Проверьте, что standby scheduler не создаёт run/jobs.
8. Выполните controlled failover в служебной группе.

### Rollback

Перед downgrade остановите обе площадки и разрешите все uncertain attempts.

```bash
alembic downgrade 8a2c4e6f0b3d
```

После downgrade запуск двух worker запрещён: версия 2.2 не содержит межплощадочный execution fence.

## 25. Обновление 2.3.0 → 2.4.0

1. Завершите или отмените открытые failover requests.
2. Убедитесь, что active lease находится на исходной production-площадке.
3. Остановите API/worker на active и standby.
4. Создайте подписанную резервную копию и выполните verify.
5. Разверните код 2.4.0 на обеих площадках.
6. Выполните:

```bash
alembic upgrade head
alembic current
```

Ожидаемая ревизия:

```text
ac4e6f8b2d5f
```

7. Настройте на обеих площадках одинаковые continuity-параметры:

```env
TELEFLOW_CONTINUITY_ASSURANCE_REQUIRED=true
TELEFLOW_CONTINUITY_REQUIRE_DISTINCT_SIGNOFF=true
TELEFLOW_CONTINUITY_DEFAULT_REQUIRE_LIVE_DRILL=true
TELEFLOW_CONTINUITY_DEFAULT_MAX_RTO_SECONDS=300
TELEFLOW_CONTINUITY_DEFAULT_EVIDENCE_VALID_DAYS=30
TELEFLOW_CONTINUITY_SYNC_INTERVAL_SECONDS=15
```

8. Запустите миграцию, затем active API/worker и standby API/worker.
9. Проверьте совпадение runtime fingerprint в разделе «Непрерывность».
10. Выполните simulation.
11. После отдельного одобрения проведите live failover/failback в служебном окне.
12. Подпишите evidence другим Owner/Admin.

Rollback migration:

```bash
alembic downgrade 9b3d5f7a1c4e
```

Rollback разрешён только после возврата active lease на исходную площадку и завершения continuity-drills.

## Обновление 2.4 → 2.5

1. Создайте проверенный backup и выполните recovery verification.
2. Создайте change request на версию 2.5.0 и переведите платформу в maintenance mode.
3. Обновите tracked-файлы приложения.
4. Выполните `alembic upgrade head`.
5. Проверьте head `bd5f7a9c3e6f` и `alembic check`.
6. Запустите `python scripts/validate_release_assets.py`.
7. Запустите `python scripts/check_function_docs.py`.
8. Откройте раздел «Нагрузка и лимиты».
9. Сначала включите policy в monitor-only режиме и создайте assessment.
10. Проведите load dry-run без Telegram network calls.
11. Включите admission и dispatch gates.
12. Выполните canary в собственной служебной группе.
13. Завершите post-change verification.

Production обязательно требует:

```env
TELEFLOW_CAPACITY_ASSURANCE_REQUIRED=true
```

Подробные переменные перечислены в [CAPACITY_ASSURANCE.md](CAPACITY_ASSURANCE.md) и `deploy/.env.production.example`.

### Откат

Перед downgrade убедитесь, что нет незавершённых jobs, зависящих от новой policy. Переведите систему в maintenance mode, сохраните evidence, затем выполните:

```bash
alembic downgrade ac4e6f8b2d5f
```

Downgrade удаляет таблицы capacity evidence; архив audit/backup должен быть создан заранее.
