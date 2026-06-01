"""
Global exception types and FastAPI exception handlers.

Never exposes stack traces to API clients.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

TRACE_HEADER = "X-Trace-Id"


class DatabaseUnavailable(Exception):
    """Raised when the persistence layer cannot be reached."""


def _trace_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", None) or uuid.uuid4())


def _error_body(
    request: Request,
    *,
    error: str,
    message: str,
    status_code: int,
) -> JSONResponse:
    trace_id = _trace_id(request)
    content: Dict[str, Any] = {
        "error": error,
        "message": message,
        "trace_id": trace_id,
    }
    response = JSONResponse(status_code=status_code, content=content)
    response.headers[TRACE_HEADER] = trace_id
    return response


def register_exception_handlers(application: FastAPI) -> None:
    """Attach production-safe exception handlers to the FastAPI app."""

    @application.exception_handler(DatabaseUnavailable)
    async def database_unavailable_handler(
        request: Request,
        _exc: DatabaseUnavailable,
    ) -> JSONResponse:
        logger.error(
            "database unavailable trace_id=%s path=%s",
            _trace_id(request),
            request.url.path,
        )
        return _error_body(
            request,
            error="DATABASE_UNAVAILABLE",
            message="Database temporarily unavailable",
            status_code=503,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        logger.warning(
            "validation error trace_id=%s path=%s errors=%s",
            _trace_id(request),
            request.url.path,
            exc.errors(),
        )
        return _error_body(
            request,
            error="VALIDATION_ERROR",
            message="Request payload is invalid",
            status_code=422,
        )

    @application.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        detail = exc.detail
        message = detail if isinstance(detail, str) else "Request could not be processed"
        return _error_body(
            request,
            error="HTTP_ERROR",
            message=message,
            status_code=exc.status_code,
        )

    @application.exception_handler(StarletteHTTPException)
    async def starlette_http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        detail = exc.detail
        message = detail if isinstance(detail, str) else "Request could not be processed"
        return _error_body(
            request,
            error="HTTP_ERROR",
            message=message,
            status_code=exc.status_code,
        )

    @application.exception_handler(Exception)
    async def unexpected_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception(
            "unexpected error trace_id=%s path=%s",
            _trace_id(request),
            request.url.path,
        )
        return _error_body(
            request,
            error="INTERNAL_ERROR",
            message="An unexpected error occurred",
            status_code=500,
        )
