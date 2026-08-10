# Интеграции TeleFlow Platform

## 1. Durable outbox

Интеграции не вызываются внутри основного HTTP/webhook transaction. Сначала создаётся OutboxEvent, затем worker доставляет его endpoints.

Event содержит:

- event ID/type;
- aggregate type/ID;
- payload snapshot;
- target endpoint IDs;
- per-endpoint delivery state;
- attempt/max/due/error/status.

Это предотвращает потерю события при временной недоступности внешней системы.

## 2. Event types

Типичные события:

- `candidate.created`;
- `candidate.updated`;
- `conversation.handoff`;
- `conversation.closed`;
- `privacy.completed`;
- test/external events.

Endpoint с пустым `event_types` может трактоваться как общий в зависимости от service configuration; для production задавайте явный allowlist.

## 3. HMAC webhook

Config:

```json
{
  "url": "https://crm.example.com/hooks/teleflow",
  "secret": "shared random secret"
}
```

Headers:

```text
X-TeleFlow-Event-ID
X-TeleFlow-Event-Type
X-TeleFlow-Signature: sha256=<hmac>
```

Получатель должен:

1. прочитать raw body;
2. вычислить HMAC-SHA256;
3. constant-time compare;
4. проверить event ID idempotency;
5. вернуть 2xx только после durable commit.

Redirects и private IP не должны использоваться. Production URL — HTTPS.

## 4. Google Sheets

Config содержит service-account JSON, spreadsheet ID и range. Service account должен иметь доступ только к нужной таблице.

Рекомендации:

- отдельный service account для TeleFlow;
- не использовать owner personal credentials;
- ротировать key;
- ограничить spreadsheet sharing;
- не экспортировать phone/email без бизнес-основания и consent;
- при 429/5xx outbox повторит событие.

Google Sheets append не обеспечивает универсальную идемпотентность. Добавляйте event ID в строку и удаляйте дубли на стороне consumer/process.

## 5. CSV export endpoint

Каждый event создаёт отдельный object:

```text
integrations/events/<event-id>.csv
```

Это исключает lost update при нескольких workers. Для сводного файла используйте downstream job, который объединяет immutable events.

## 6. Partial delivery

Если событие направлено двум endpoints и первый успешно завершён, а второй вернул временную ошибку:

- event получает `retry`;
- state первого остаётся `delivered`;
- при следующем цикле первый не вызывается;
- повторяется только второй.

## 7. Dead letters

После `max_attempts` event становится `dead`. Действия:

1. проверить endpoint/status/credentials;
2. проверить, не принял ли remote event несмотря на timeout;
3. проверить idempotency event ID;
4. исправить проблему;
5. выполнить manual retry;
6. зафиксировать incident при потере/дубле.

## 8. Service API

API keys создаются владельцем, имеют scopes и expiry. Secret показывается один раз.

Пример:

```bash
curl -H 'Authorization: Bearer tfp_...' \
  https://panel.example.com/api/v1/external/candidates
```

Не используйте wildcard scope для обычной CRM. Для контактов выдавайте отдельный `candidates:contacts` только сервису, которому они действительно нужны.

## 9. SSRF controls

TeleFlow проверяет scheme, credentials, DNS и IP. Private/internal endpoints запрещены по умолчанию. При необходимости private integration включается только вместе с egress firewall и allowlist на уровне сети.
