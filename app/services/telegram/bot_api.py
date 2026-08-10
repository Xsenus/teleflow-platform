from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.enums import DestinationKind, ParseMode
from app.services.telegram.base import (
    TelegramDestinationInfo,
    TelegramGateway,
    TelegramIdentity,
    TelegramSendResult,
)
from app.services.telegram.errors import (
    TelegramAuthError,
    TelegramDeliveryUncertain,
    TelegramFloodWait,
    TelegramInvalidRequest,
    TelegramNotFound,
    TelegramTransientError,
    TelegramWriteForbidden,
)


class BotApiGateway(TelegramGateway):
    def __init__(self, bot_token: str, *, timeout_seconds: float = 30.0):
        """Инициализировать BotApiGateway with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._timeout_seconds = timeout_seconds
        self._identity: TelegramIdentity | None = None

    def _call(
        self,
        method: str,
        *,
        json_payload: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Реализовать внутренний этап call step класса BotApiGateway. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                if files:
                    response = client.post(
                        f"{self._base_url}/{method}", data=data or {}, files=files
                    )
                else:
                    response = client.post(
                        f"{self._base_url}/{method}", json=json_payload or data or {}
                    )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise TelegramTransientError("Telegram Bot API временно недоступен") from exc
        except (
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.ReadError,
            httpx.WriteError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise TelegramDeliveryUncertain(
                "Не удалось подтвердить результат запроса Telegram; проверьте чат вручную"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            if method.startswith("send"):
                raise TelegramDeliveryUncertain(
                    "Telegram вернул неполный результат отправки; проверьте чат вручную"
                ) from exc
            raise TelegramTransientError("Telegram Bot API вернул некорректный ответ") from exc

        if response.status_code == 429:
            retry_after = int(payload.get("parameters", {}).get("retry_after", 60))
            raise TelegramFloodWait(
                "Telegram потребовал остановить отправку", retry_after=retry_after
            )
        if response.status_code == 401 or (response.status_code == 404 and method == "getMe"):
            raise TelegramAuthError("Bot token недействителен или отозван")
        if response.status_code == 403:
            raise TelegramWriteForbidden(
                str(payload.get("description") or "Боту запрещено писать в чат")
            )
        if response.status_code >= 500:
            if method.startswith("send"):
                raise TelegramDeliveryUncertain(
                    "Telegram не подтвердил результат отправки; проверьте чат вручную"
                )
            raise TelegramTransientError("Временная ошибка Telegram Bot API")
        if not payload.get("ok"):
            description = str(payload.get("description") or "Ошибка Telegram Bot API")
            lowered = description.lower()
            if "chat not found" in lowered:
                raise TelegramNotFound("Чат не найден или недоступен боту")
            if "not enough rights" in lowered or "have no rights" in lowered:
                raise TelegramWriteForbidden(description)
            raise TelegramInvalidRequest(description)
        return payload["result"]

    def get_identity(self) -> TelegramIdentity:
        """Прочитать identity класса BotApiGateway. Значение возвращается без несвязанных изменений
        состояния.
        """
        if self._identity is not None:
            return self._identity
        result = self._call("getMe")
        display_name = " ".join(
            part for part in [result.get("first_name"), result.get("last_name")] if part
        )
        self._identity = TelegramIdentity(
            account_id=int(result["id"]),
            username=result.get("username"),
            display_name=display_name or result.get("username") or str(result["id"]),
            is_bot=True,
        )
        return self._identity

    def resolve_destination(
        self, *, chat_id: int | None, username: str | None, topic_id: int | None = None
    ) -> TelegramDestinationInfo:
        """Прочитать destination класса BotApiGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        target: int | str
        if chat_id is not None:
            target = chat_id
        elif username:
            target = username if username.startswith("@") else f"@{username}"
        else:
            raise TelegramNotFound("Не указан чат")
        result = self._call("getChat", json_payload={"chat_id": target})
        chat_type = result.get("type")
        kind_map = {
            "group": DestinationKind.GROUP,
            "supergroup": DestinationKind.SUPERGROUP,
            "channel": DestinationKind.CHANNEL,
        }
        if chat_type not in kind_map:
            raise TelegramInvalidRequest("Личные чаты не поддерживаются как назначения")
        kind = DestinationKind.FORUM_TOPIC if topic_id else kind_map[chat_type]
        capabilities: dict[str, Any] = {
            "is_forum": bool(result.get("is_forum")),
            "can_send_messages": True,
        }
        try:
            me = self.get_identity()
            member = self._call(
                "getChatMember",
                json_payload={"chat_id": result["id"], "user_id": me.account_id},
            )
            capabilities["membership_status"] = member.get("status")
            capabilities["can_post_messages"] = member.get("can_post_messages")
            status = member.get("status")
            if kind == DestinationKind.CHANNEL:
                capabilities["can_send_messages"] = status == "creator" or (
                    status == "administrator" and bool(member.get("can_post_messages"))
                )
            else:
                capabilities["can_send_messages"] = status in {
                    "creator",
                    "administrator",
                    "member",
                } or (status == "restricted" and bool(member.get("can_send_messages")))
        except (TelegramInvalidRequest, TelegramNotFound, TelegramWriteForbidden):
            pass
        return TelegramDestinationInfo(
            chat_id=int(result["id"]),
            username=result.get("username"),
            title=result.get("title") or str(result["id"]),
            kind=kind,
            capabilities=capabilities,
        )

    def list_destinations(self) -> list[TelegramDestinationInfo]:
        # Bot API intentionally has no endpoint that lists every group containing a bot.
        """Прочитать destinations класса BotApiGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        return []

    def send_message(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        body: str,
        parse_mode: ParseMode,
        link_preview: bool,
        media_path: Path | None = None,
        media_content_type: str | None = None,
    ) -> TelegramSendResult:
        """Выполнить операцию send message класса BotApiGateway. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        parse_value = {
            ParseMode.PLAIN: None,
            ParseMode.HTML: "HTML",
            ParseMode.MARKDOWN: "MarkdownV2",
        }[parse_mode]
        common: dict[str, Any] = {"chat_id": chat_id}
        if topic_id:
            common["message_thread_id"] = topic_id
        if parse_value:
            common["parse_mode"] = parse_value

        if media_path:
            is_photo = (media_content_type or "").startswith("image/")
            method = "sendPhoto" if is_photo else "sendDocument"
            field_name = "photo" if is_photo else "document"
            data = {**common, "caption": body}
            with media_path.open("rb") as handle:
                result = self._call(
                    method,
                    data={key: str(value) for key, value in data.items()},
                    files={field_name: (media_path.name, handle, media_content_type)},
                )
        else:
            payload: dict[str, Any] = {
                **common,
                "text": body,
                "link_preview_options": {"is_disabled": not link_preview},
            }
            result = self._call("sendMessage", json_payload=payload)
        return TelegramSendResult(
            message_id=str(result["message_id"]), date=str(result.get("date"))
        )

    def set_webhook(
        self,
        *,
        url: str,
        secret_token: str,
        allowed_updates: list[str],
        drop_pending_updates: bool = False,
    ) -> bool:
        """Обновить webhook класса BotApiGateway. Переход применяется только после проверки его
        предусловий.
        """
        result = self._call(
            "setWebhook",
            json_payload={
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": allowed_updates,
                "drop_pending_updates": drop_pending_updates,
            },
        )
        return bool(result)

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        """Безопасно выполнить delete webhook класса BotApiGateway. Зависимое состояние и видимые в
        аудите последствия обрабатываются согласованно.
        """
        return bool(
            self._call(
                "deleteWebhook",
                json_payload={"drop_pending_updates": drop_pending_updates},
            )
        )

    def get_webhook_info(self) -> dict[str, Any]:
        """Прочитать webhook info класса BotApiGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        result = self._call("getWebhookInfo")
        return result if isinstance(result, dict) else {}

    def get_business_connection(self, business_connection_id: str) -> dict[str, Any]:
        """Прочитать business connection класса BotApiGateway. Значение возвращается без
        несвязанных изменений состояния.
        """
        result = self._call(
            "getBusinessConnection",
            json_payload={"business_connection_id": business_connection_id},
        )
        return result if isinstance(result, dict) else {}

    def send_business_message(
        self,
        *,
        business_connection_id: str,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Выполнить операцию send business message класса BotApiGateway. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        payload: dict[str, Any] = {
            "business_connection_id": business_connection_id,
            "chat_id": chat_id,
            "text": text,
            "link_preview_options": {"is_disabled": True},
        }
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        result = self._call("sendMessage", json_payload=payload)
        return result if isinstance(result, dict) else {}

    def read_business_message(
        self,
        *,
        business_connection_id: str,
        chat_id: int,
        message_id: int,
    ) -> bool:
        """Прочитать business message класса BotApiGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        return bool(
            self._call(
                "readBusinessMessage",
                json_payload={
                    "business_connection_id": business_connection_id,
                    "chat_id": chat_id,
                    "message_id": message_id,
                },
            )
        )
