# Участие в разработке TeleFlow

## Подготовка окружения

Требуются Python 3.12+, Node.js 22+ и Git. Создайте виртуальное окружение и установите
фиксированные зависимости:

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
```

На Windows используйте `.venv\\Scripts\\python.exe` вместо `.venv/bin/python`.

## Обязательные проверки

Перед pull request выполните:

```bash
ruff check .
ruff format --check .
mypy app
python scripts/check_function_docs.py
pip-audit -r requirements.txt
python scripts/run_pytest.py -q
```

Полный Linux release-gate запускается командой `./scripts/qa_release.sh`. Он дополнительно
проверяет миграции, JavaScript и shell-синтаксис, HTTP smoke, worker, audit-chain,
ротацию ключа и recovery drill.

## Правила изменений

- Новое поведение сопровождается тестом, воспроизводящим пользовательский или safety-сценарий.
- Функции и методы документируются по-русски; технические идентификаторы можно не переводить.
- Миграции Alembic должны поддерживать upgrade и downgrade до предыдущего release head.
- Секреты, реальные Telegram session, базы данных и пользовательские выгрузки не коммитятся.
- Изменения API, конфигурации и эксплуатации отражаются в README, `.env.example` и `docs/`.
- UI проверяется минимум на ширинах 390, 768 и 1440 пикселей.

## Pull request

Делайте небольшие тематические коммиты. В описании PR укажите причину, риски, способ
проверки и влияние на миграции, безопасность, производительность и обратную совместимость.
