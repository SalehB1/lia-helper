"""Application exceptions. Every default message is Persian and safe to show a user."""

from __future__ import annotations


class AppException(Exception):
    """Base error for expected, user-facing failures."""

    def __init__(
        self,
        message: str = "خطایی در پردازش درخواست رخ داد.",
        code: str = "APP_ERROR",
        status_code: int = 400,
        details: dict | None = None,
    ) -> None:
        """Build the error.

        Args:
            message: Persian, user-safe text. Never include internals here.
            code: Stable machine-readable code for the frontend.
            status_code: HTTP status the handler should return.
            details: Optional JSON-serializable extra context.
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details


class NotFoundException(AppException):
    """The requested resource does not exist for this session."""

    def __init__(
        self,
        message: str = "موردی که خواستید پیدا نشد.",
        code: str = "NOT_FOUND",
        status_code: int = 404,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, code, status_code, details)


class ValidationException(AppException):
    """The request payload or headers are not acceptable."""

    def __init__(
        self,
        message: str = "داده‌های ارسالی معتبر نیست.",
        code: str = "VALIDATION_ERROR",
        status_code: int = 422,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, code, status_code, details)


class ForbiddenException(AppException):
    """The caller may not touch this resource."""

    def __init__(
        self,
        message: str = "دسترسی به این بخش مجاز نیست.",
        code: str = "FORBIDDEN",
        status_code: int = 403,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, code, status_code, details)


class UnauthorizedException(AppException):
    """No usable session identity was supplied."""

    def __init__(
        self,
        message: str = "برای این درخواست باید شناسهٔ نشست معتبر ارسال شود.",
        code: str = "UNAUTHORIZED",
        status_code: int = 401,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, code, status_code, details)


class RateLimitException(AppException):
    """Too many requests from this session or IP."""

    def __init__(
        self,
        message: str = "تعداد درخواست‌ها زیاد است؛ لطفاً کمی بعد دوباره تلاش کنید.",
        code: str = "RATE_LIMITED",
        status_code: int = 429,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, code, status_code, details)


class ServiceUnavailableException(AppException):
    """A dependency (LLM provider, embeddings, disk) is unavailable."""

    def __init__(
        self,
        message: str = "سرویس در حال حاضر در دسترس نیست؛ کمی بعد دوباره تلاش کنید.",
        code: str = "SERVICE_UNAVAILABLE",
        status_code: int = 503,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, code, status_code, details)
