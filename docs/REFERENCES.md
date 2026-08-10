# Официальные источники

Проверено: 5 августа 2026 года.

## Telegram Bot API

- Bot API: https://core.telegram.org/bots/api
- Bot FAQ и технические limits: https://core.telegram.org/bots/faq
- Bot API changelog: https://core.telegram.org/bots/api-changelog

Используется для identity, destination checks, text/media/topic delivery, webhook management и Telegram Business methods.

## Telegram Business

- Business connections/messages в Bot API: https://core.telegram.org/bots/api#businessconnection
- `getBusinessConnection`: https://core.telegram.org/bots/api#getbusinessconnection
- `setWebhook` и secret token: https://core.telegram.org/bots/api#setwebhook
- Telegram Business overview: https://telegram.org/blog/telegram-business
- Chat Automation announcement: https://telegram.org/blog/ai-bot-revolution-11-new-features

Архитектурное решение: Business webhook предназначен для входящих личных диалогов и ответов от connected bot, а не заменяет очередь групповых публикаций.

## Telegram User API / MTProto

- Получение `api_id`/`api_hash`: https://core.telegram.org/api/obtaining_api_id
- RPC errors: https://core.telegram.org/api/errors
- User API Terms: https://telegram.org/tos/userapi
- Spam FAQ: https://telegram.org/faq_spam

Архитектурное решение: используется отдельный рабочий аккаунт владельца. FloodWait/anti-spam останавливают connection; bypass functionality отсутствует.

## Telethon

- Documentation: https://docs.telethon.dev/
- Sessions: https://docs.telethon.dev/en/stable/concepts/sessions.html
- RPC errors: https://docs.telethon.dev/en/stable/concepts/errors.html
- PyPI Telethon 1.44.0: https://pypi.org/project/Telethon/1.44.0/

Telethon изолирован внутри MTProto gateway.

## FastAPI/Pydantic/SQLAlchemy/Alembic

- FastAPI: https://fastapi.tiangolo.com/
- Pydantic: https://docs.pydantic.dev/
- SQLAlchemy 2: https://docs.sqlalchemy.org/en/20/
- Alembic: https://alembic.sqlalchemy.org/

## Security/observability/storage

- OWASP ASVS: https://owasp.org/www-project-application-security-verification-standard/
- OWASP SSRF Prevention: https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- Prometheus: https://prometheus.io/docs/
- OpenTelemetry: https://opentelemetry.io/docs/
- Sentry Python: https://docs.sentry.io/platforms/python/
- Amazon S3 API concepts: https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html
- age encryption: https://age-encryption.org/

## Примечание

Ссылки используются как технические первоисточники. Организация самостоятельно проверяет применимое законодательство, правила конкретных Telegram-групп и договоры обработки данных внешних AI/CRM providers.
