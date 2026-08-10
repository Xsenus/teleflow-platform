# TeleFlow Platform 1.5.0 — Pilot Certification & Diagnostics

Дата релиза: 6 августа 2026 года.

## Основное

Версия 1.5 переводит масштабирование live-публикаций из ручной инструкции в серверный workflow:

- этапы `LOCAL → SERVICE → FIVE → TWENTY → FIFTY → HUNDRED`;
- жёсткий route limit в preview, run-now, resume, scheduler и Safety Engine;
- фиксированная one-shot canary без пользовательского рекламного текста и autoretry;
- сохраняемая оценка следующего этапа с TTL и fingerprint;
- доказательство реальной доставки предыдущего этапа;
- fake message ID не считается live-доказательством;
- понижение этапа приостанавливает несовместимые кампании;
- обезличенный временный support bundle с внутренним manifest;
- отдельный веб-интерфейс сертификации, истории canary и diagnostics;
- production startup gate для обязательного stage enforcement;
- canary подчиняется organization emergency stop до создания gateway;
- assessment fingerprint инвалидируется при новом critical event или потере worker heartbeat;
- destination засчитывается только вместе со своим собственным healthy connection;
- исправлены Docker/Compose version labels и обязательные production pilot flags.

## База данных

Новый Alembic head:

```text
e2f4a6c8d0b1
```

Добавлены:

- поля pilot stage в `organizations`;
- `pilot_stage_assessments`;
- `pilot_canary_attempts`;
- `support_bundles`.

Проверяется downgrade к head 1.4 `c9e1f3a5b7d9` и повторный upgrade.

## Security

- canary destination и connection блокируются `FOR UPDATE` перед cooldown/cap check;
- canary выполняет не более одного сетевого запроса;
- support bundle исключает credentials, PII, message bodies и внутренние имена;
- скачивание support bundle использует `no-store` и SHA-256 verification;
- этап меняется только Owner/Admin с точной confirm-фразой;
- fingerprint предотвращает применение устаревшей оценки после изменения worker, critical notifications, connections, permissions, canary или run evidence;
- Operator/Viewer не могут отправить canary, изменить этап или получить support bundle.

## Совместимость

Новые организации начинают с `LOCAL`. При миграции существующая организация получает `HUNDRED`, чтобы обновление не остановило прежний маршрут без решения владельца. Для строгой повторной сертификации владелец вручную понижает этап и проходит workflow последовательно.

## Ограничения внешней приёмки

Релиз не утверждает как проверенные без credentials владельца:

- реальную Bot API/MTProto canary;
- фактическую доставку в служебную группу;
- переходы масштаба на реальных Telegram-данных;
- Docker image build и production TLS в текущем контейнере;
- браузерную приёмку в управляемом Chromium, где действует системная URLBlocklist.
