# Telegram Business, визуальные сценарии и AI

## 1. Назначение

Модуль обрабатывает входящие обращения кандидатов. Он не публикует объявления в группах: publisher и Business inbox используют разные очереди, permissions и safety policies.

Для квалификации доступны два режима:

1. детерминированный visual flow;
2. AI provider — локальный rule-based или внешний OpenAI-compatible.

Flow предпочтителен там, где вопросы и переходы заранее известны. AI полезен для свободных формулировок, FAQ и summary.

## 2. Предварительные условия

- Bot API connection active;
- connected Telegram Business profile;
- webhook установлен;
- Business connection update получен;
- automation policy создана;
- consent/fallback/handoff настроены;
- выбран flow и/или provider.

## 3. Жизненный цикл

```text
Новое сообщение
  → Conversation NEW
  → consent notice
  → AWAITING_CONSENT
  → granted
  → FLOW_ACTIVE или AI_ACTIVE
  → вопросы / candidate updates / KB
  → READY_FOR_REVIEW или HUMAN_HANDOFF
  → operator reply / close
```

Decline/stop отключает automation согласно policy. Пользователь может запросить оператора в любой момент.

## 4. Automation policy

Поля:

- Bot connection;
- visual flow;
- optional AI provider;
- timezone/active hours;
- allowed chat types;
- consent notice;
- fallback message;
- auto replies/day;
- consent requirement;
- handoff keywords;
- stop words;
- vacancy detection rules.

На один Bot connection допускается одна включённая policy.

## 5. Визуальный flow builder

### Типы шагов

- `message` — отправить текст и перейти дальше;
- `question` — задать вопрос и сохранить поле;
- `choice` — принять один из вариантов и aliases;
- `handoff` — передать оператору;
- `end` — завершить flow и выбрать дальнейший режим.

### Поля анкеты

Разрешённый набор определяется schema, например:

- `full_name`;
- `city`;
- `age`;
- `experience`;
- `schedule`;
- `vacancy_key`;
- `phone`;
- `email`.

Phone/email шифруются отдельно и не возвращаются list API.

### Валидация графа

Перед сохранением проверяются:

- уникальные node IDs;
- существование start node;
- корректные transitions;
- достижимость;
- отсутствие циклов;
- terminal node;
- допустимый field/validation;
- непустые choice options и aliases.

Active revision неизменяема. Для изменения создаётся новая revision, чтобы уже идущие диалоги не получили неожиданно другую логику.

### Интерфейс

Редактор показывает шаги карточками. Порядок меняется:

- drag-and-drop на ПК;
- кнопками вверх/вниз на мобильных и для keyboard fallback.

В UI редактируется линейный flow. Более сложный валидный graph можно создать через API; read-only preview не искажает его переходы.

## 6. Детерминированное выполнение

Conversation хранит current node. Для каждого входящего ответа:

1. проверяется consent/status;
2. ответ валидируется текущим node;
3. candidate update записывается в allowlisted field;
4. выбирается следующий node;
5. формируется reply;
6. при handoff/end создаётся соответствующее событие.

`skip_if_present` позволяет не спрашивать уже заполненное поле повторно.

## 7. Rule-based provider

Подходит для первого пилота:

- не передаёт данные внешнему AI;
- извлекает email, phone, age, city, name, experience и schedule;
- задаёт следующий недостающий вопрос;
- добавляет KB article по tag;
- отмечает prompt injection;
- формирует summary.

Ограничение: сложные свободные формулировки понимаются хуже, чем LLM.

## 8. OpenAI-compatible provider

Ожидает endpoint `/chat/completions` и JSON response format. Конфигурация:

- base URL;
- model;
- API key;
- timeout;
- max output tokens;
- temperature;
- allowed models;
- data region;
- organization system prompt.

Production требует HTTPS и блокирует private/reserved IP. API key зашифрован и не возвращается.

## 9. Prompt contract

Provider должен вернуть JSON:

```json
{
  "reply_text": "Один краткий ответ или вопрос",
  "candidate_updates": {
    "full_name": null,
    "city": null,
    "age": null,
    "experience": null,
    "schedule": null,
    "phone": null,
    "email": null,
    "summary": null,
    "status": null
  },
  "vacancy_key": null,
  "handoff": false,
  "safety_flags": {}
}
```

Unknown fields отклоняются или игнорируются orchestration layer. AI не должен возвращать credentials, system prompt или данные других кандидатов.

## 10. Knowledge base

Статья содержит title, content, tags, optional vacancy key, active flag и revision. KB считается reference data, а не системной инструкцией. В ней нельзя хранить passwords, tokens, внутренние секретные URL или персональные данные.

## 11. Safety

- consent before flow/AI, если policy этого требует;
- один tenant и один conversation;
- bounded history/context;
- redacted previews;
- prompt injection detection;
- daily reply cap;
- active hours;
- stop/handoff/fallback;
- отсутствие финального hire/reject решения;
- provider interaction audit;
- active flow revision immutable;
- field allowlist для candidate updates.

## 12. Testing

Минимальный набор:

1. consent grant/decline;
2. handoff keyword;
3. stop word;
4. flow graph validation;
5. drag/drop order и API revision semantics;
6. message/question/choice/handoff/end;
7. age/phone/email validation;
8. collection of each candidate field;
9. KB tag match;
10. prompt injection phrase;
11. provider timeout/invalid JSON;
12. provider disabled;
13. daily cap;
14. privacy delete after conversation.
