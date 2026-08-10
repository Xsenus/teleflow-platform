from __future__ import annotations


class TelegramGatewayError(Exception):
    code = "TELEGRAM_ERROR"
    transient = False
    requires_review = False

    def __init__(self, message: str, *, retry_after: int | None = None):
        """Инициализировать TelegramGatewayError with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        super().__init__(message)
        self.retry_after = retry_after


class TelegramDependencyMissing(TelegramGatewayError):
    code = "TELEGRAM_DEPENDENCY_MISSING"


class TelegramAuthError(TelegramGatewayError):
    code = "TELEGRAM_AUTH_INVALID"
    requires_review = True


class TelegramFloodWait(TelegramGatewayError):
    code = "FLOOD_WAIT"
    transient = True
    requires_review = True


class TelegramSlowModeWait(TelegramGatewayError):
    code = "SLOWMODE_WAIT"
    transient = True


class TelegramWriteForbidden(TelegramGatewayError):
    code = "CHAT_WRITE_FORBIDDEN"
    requires_review = True


class TelegramAntiSpamRestriction(TelegramGatewayError):
    code = "ANTI_SPAM_RESTRICTION"
    requires_review = True


class TelegramNotFound(TelegramGatewayError):
    code = "DESTINATION_NOT_FOUND"


class TelegramTransientError(TelegramGatewayError):
    code = "TELEGRAM_TRANSIENT"
    transient = True


class TelegramDeliveryUncertain(TelegramGatewayError):
    """The request may have reached Telegram, so automatic retry could duplicate a post."""

    code = "DELIVERY_RESULT_UNCERTAIN"
    requires_review = True


class TelegramInvalidRequest(TelegramGatewayError):
    code = "TELEGRAM_BAD_REQUEST"
