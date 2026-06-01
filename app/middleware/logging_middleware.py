"""
Structured request logging middleware.

Every API request logs trace_id, store_id, endpoint, latency, event_count, and status.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import log_structured

logger = logging.getLogger(__name__)

TRACE_HEADER = "X-Trace-Id"


def _extract_store_id(request: Request) -> Optional[str]:
    """Resolve store_id from path params or request state."""
    store_id = request.path_params.get("store_id")
    if store_id:
        return str(store_id)
    state_store = getattr(request.state, "store_id", None)
    if state_store:
        return str(state_store)
    return None


def _resolve_event_count(request: Request, response: Response) -> int:
    """Determine how many events were involved in the request."""
    state_count = getattr(request.state, "event_count", None)
    if state_count is not None:
        return int(state_count)

    if request.url.path.rstrip("/") != "/events/ingest":
        return 0

    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        return 0

    body = getattr(response, "body", None)
    if not body:
        return 0

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return 0

    accepted = int(payload.get("accepted", 0))
    duplicates = int(payload.get("duplicates", 0))
    rejected = int(payload.get("rejected", 0))
    return accepted + duplicates + rejected


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Log request start and completion as JSON with a per-request trace_id."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        trace_id = str(uuid.uuid4())
        request.state.trace_id = trace_id
        endpoint = request.url.path
        store_id = _extract_store_id(request)
        started = time.perf_counter()

        log_structured(
            logger,
            logging.INFO,
            "request started",
            trace_id=trace_id,
            store_id=store_id,
            endpoint=endpoint,
            method=request.method,
            phase="start",
        )

        try:
            response = await call_next(request)
        except Exception:
            latency_ms = int((time.perf_counter() - started) * 1000)
            log_structured(
                logger,
                logging.ERROR,
                "request failed",
                trace_id=trace_id,
                store_id=_extract_store_id(request),
                endpoint=endpoint,
                latency_ms=latency_ms,
                event_count=getattr(request.state, "event_count", 0) or 0,
                status_code=500,
                phase="complete",
            )
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        event_count = _resolve_event_count(request, response)
        store_id = _extract_store_id(request)

        log_structured(
            logger,
            logging.INFO,
            "request completed",
            trace_id=trace_id,
            store_id=store_id,
            endpoint=endpoint,
            latency_ms=latency_ms,
            event_count=event_count,
            status_code=response.status_code,
            phase="complete",
        )

        response.headers[TRACE_HEADER] = trace_id
        return response
