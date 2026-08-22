"""Uniform error envelope for every failure the API can produce.

Body shape is always::

    {"success": false, "error": {"code": str, "message": str, "details": dict | null}}
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import AppException
from app.core.logging import get_logger

try:  # slowapi is optional at import time so a missing extra never breaks boot.
    from slowapi.errors import RateLimitExceeded
except ImportError:  # pragma: no cover - slowapi is in requirements.txt
    RateLimitExceeded = None  # type: ignore[assignment,misc]

logger = get_logger("app.errors")

GENERIC_MESSAGE = "خطای غیرمنتظره‌ای در سرور رخ داد؛ لطفاً بعداً دوباره تلاش کنید."
VALIDATION_MESSAGE = "داده‌های ارسالی معتبر نیست."
RATE_LIMIT_MESSAGE = "تعداد درخواست‌ها زیاد است؛ لطفاً کمی بعد دوباره تلاش کنید."


def error_response(
    status_code: int, code: str, message: str, details: dict | None = None
) -> JSONResponse:
    """Build the standard error envelope.

    Args:
        status_code: HTTP status to send.
        code: Stable machine-readable error code.
        message: Persian, user-safe message.
        details: Optional JSON-serializable context.

    Returns:
        The JSON response carrying the envelope.
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {"code": code, "message": message, "details": details},
        },
    )


def _safe_errors(errors: list[dict]) -> list[dict]:
    """Strip the echoed payload out of pydantic's error entries.

    Each entry carries the offending ``input`` verbatim, which turns a large invalid body
    into a bandwidth amplifier and reflects whatever the caller sent. ``loc``/``msg``/
    ``type``/``ctx`` are all the panel needs to explain the problem.

    Args:
        errors: Raw ``RequestValidationError.errors()`` entries.

    Returns:
        The same entries without ``input`` or ``url``.
    """
    return [
        {key: value for key, value in error.items() if key not in ("input", "url")}
        for error in jsonable_encoder(errors)
    ]


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every error handler to the application.

    Args:
        app: The FastAPI application to configure.
    """

    @app.exception_handler(AppException)
    async def _app_exception(request: Request, exc: AppException) -> JSONResponse:
        logger.info(
            "app_exception",
            code=exc.code,
            status_code=exc.status_code,
            path=request.url.path,
        )
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(
            422,
            "VALIDATION_ERROR",
            VALIDATION_MESSAGE,
            {"errors": _safe_errors(exc.errors())},
        )

    if RateLimitExceeded is not None:

        @app.exception_handler(RateLimitExceeded)
        async def _rate_limited(
            request: Request, exc: Exception
        ) -> JSONResponse:
            return error_response(429, "RATE_LIMITED", RATE_LIMIT_MESSAGE)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log the full traceback server-side; the client sees nothing internal.
        logger.error(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            exc_info=exc,
        )
        return error_response(500, "INTERNAL_ERROR", GENERIC_MESSAGE)
