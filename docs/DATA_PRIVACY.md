# Персональные данные и privacy

## 1. Какие данные обрабатываются

В зависимости от policy:

- Telegram user/chat identifiers;
- username/name;
- текст диалога;
- phone/email;
- город, возраст, опыт, график;
- vacancy/source/status/summary;
- consent timestamps;
- technical update/message IDs.

Организация отвечает за правовое основание, уведомление пользователя и сроки хранения в своей юрисдикции.

## 2. Минимизация

- AI включается после consent, если policy это требует;
- list endpoints показывают redacted preview;
- full body/contact раскрываются отдельно;
- raw Telegram update имеет короткий retention;
- external API scopes разделяют profile/contact;
- AI context ограничен текущим диалогом;
- audit не хранит raw PII.

## 3. Encryption

Full message body, contacts и raw update payload шифруются AES-GCM. Metadata, status и redacted preview остаются доступны для работы/индексации; это следует учитывать при классификации БД backup.

## 4. Consent

Состояния:

```text
unknown → requested → granted
                    ↘ declined
         granted → revoked
```

Consent notice должен объяснять цель обработки, основные данные, автоматизацию/AI, передачу оператору и способ отказаться. Consent Telegram message ID/time сохраняются в conversation metadata/audit flow.

## 5. Export

Privacy export можно создать по:

- conversation ID;
- Telegram user ID;
- Telegram chat ID.

Export содержит относящиеся к subject данные текущей организации. Файл шифруется, имеет TTL и выдаётся через authenticated endpoint.

## 6. Delete

Delete выполняет scrubbing:

- message full bodies;
- phone/email;
- user-facing identifiers;
- candidate PII;
- raw inbound payload.

Технические IDs/status/audit могут сохраняться в минимальном виде для целостности и безопасности. Это не юридическая интерпретация требований конкретной страны; retention policy должна быть согласована организацией.

## 7. Retention

Параметры:

- organization conversation retention;
- `TELEFLOW_INBOUND_RAW_RETENTION_DAYS`;
- export TTL;
- backup retention отдельно.

Worker периодически scrub-ит expired conversations/updates/exports. Backup может содержать данные до даты создания, поэтому deletion procedure должна учитывать backup lifecycle и невозможность выборочного изменения immutable archive.

## 8. External providers

До включения AI/Google/webhook необходимо оценить:

- data processing agreement;
- region;
- provider retention/training policy;
- subprocessors;
- encryption;
- deletion process;
- transfer restrictions.

Rule-based provider позволяет выполнять базовый сценарий без передачи диалога внешнему AI.

## 9. Operator rules

- не копировать contacts в личные мессенджеры;
- не скачивать export без задачи;
- удалять local downloads;
- использовать role/scope least privilege;
- фиксировать access/incident;
- не вставлять реальные диалоги в сторонние AI tools вручную.
