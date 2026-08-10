# TeleFlow Platform 2.4.0 — Continuity Drills & Failback Assurance

Дата: **9 августа 2026 года**
Alembic head: `ac4e6f8b2d5f`
Предыдущий head: `9b3d5f7a1c4e`

## Главное

Версия 2.4 добавляет проверяемый цикл непрерывности `Primary → Standby → Primary`, независимую приёмку evidence и нулевой-сетевой simulation mode. Контур построен поверх Execution Fencing 2.3 и не создаёт альтернативного пути к Telegram API.

## Добавлено

- tenant continuity policy с RTO, TTL evidence и four-eyes;
- simulation без lease mutation и сетевых вызовов;
- live-drill на production controlled failover;
- fenced failback на исходную площадку;
- runtime evidence версии, схемы и критической конфигурации;
- semantic compatibility active/standby;
- SHA-256 event chain для каждого перехода;
- semantic evidence verification перед sign-off;
- фоновая worker-синхронизация завершённых failover requests;
- database savepoint на каждый синхронизируемый drill;
- continuity check в commissioning;
- Prometheus metrics, alerts и Grafana panels;
- 31-й раздел панели «Непрерывность»;
- API policy/drills/events/verify;
- Alembic migration `ac4e6f8b2d5f`;
- автоматический gate документации функций.

## Комментарии и поддерживаемость

Все именованные Python-функции в `app`, `scripts`, `migrations` и `tests` имеют поясняющий docstring. Все именованные функции и object methods в SPA/service worker имеют JSDoc. `scripts/check_function_docs.py` и отдельный regression test запрещают возврат недокументированных функций.

## Исправлено

- модель и миграция drill согласованы по `created_by_id`;
- фоновая синхронизация не инициирует failover и не обращается к Telegram;
- ошибка одного drill не откатывает другие организации;
- commissioning учитывает continuity compliance;
- production Compose передаёт обязательные continuity-gates;
- service worker version cache обновлён до 2.4.0;
- release-QA сериализован через `flock` и возвращает exit code 73 при параллельном запуске;
- migration round-trip обновлён до `2.4 → 2.3 → 2.4`.

## Миграция

```bash
alembic upgrade head
alembic current
```

Ожидаемый head:

```text
ac4e6f8b2d5f
```

Проверяемый rollback:

```text
2.4 ac4e6f8b2d5f
→ downgrade
2.3 9b3d5f7a1c4e
→ upgrade
2.4 ac4e6f8b2d5f
```

Перед downgrade завершите или безопасно отмените continuity-drills и убедитесь, что active lease возвращён на исходную площадку.

## Внешние границы

Без двух физических площадок, общей PostgreSQL и реальных Telegram credentials невозможно подтвердить межхостовый failover, network partition и live canary. Эти проверки перечислены в [CONTINUITY_ASSURANCE.md](CONTINUITY_ASSURANCE.md) и [PILOT_CHECKLIST.md](PILOT_CHECKLIST.md), но не подменяются fake mode.


## QA

```text
276/276 тестов
31 функциональный модуль
20 coverage-процессов
17 203 statements
3 427 missed
80,08% statement coverage
1 366 Python-функций документировано
154 именованные JavaScript-функции документированы
0 пропусков documentation gate
```

Дополнительно пройдены migration round-trip 2.4→2.3→2.4, HTTP smoke, worker continuity sync, audit-chain, dry-run ротации master key, подписанный recovery backup/verify/restore drill и повторный QA после распаковки ZIP.
