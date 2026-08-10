# TeleFlow Platform 1.9.0 — Change Management & Upgrade Assurance

Дата: 7 августа 2026 года.

## Новое

- 26-й раздел веб-панели «Изменения»;
- tenant-scoped change requests;
- типы upgrade/configuration/database migration/infrastructure;
- обязательные reason, risk summary и rollback plan;
- независимое утверждение другим Owner/Admin;
- SHA-256 fingerprint согласованного изменения;
- отдельный maintenance mode;
- scheduler/Safety/manual-run gates на время обслуживания;
- persisted pre-change и post-change verification reports;
- Alembic drift check;
- version drift check;
- active-delivery check перед изменением;
- audit-chain verification;
- worker heartbeat post-check;
- controlled `completed`, `failed`, `cancelled` lifecycle;
- запрет выхода из maintenance при активном изменении;
- migration `5d9f1b3c7e2a`.

## Safety

Релиз не добавляет функции обхода ограничений Telegram. Maintenance gate усиливает существующий publisher: запланированное техническое изменение блокирует сетевую отправку до Telegram до явного завершения обслуживания.

## Совместимость

Upgrade path:

```text
1.8 head 4c8e0a2b6d1f
→ 1.9 head 5d9f1b3c7e2a
```

Downgrade migration 1.9→1.8 поддерживается для схемы БД, но перед реальным rollback необходимо использовать documented change request + Recovery Assurance и проверить совместимость application code/data.
