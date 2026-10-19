import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.logger import logger


class LatencyAndTracingMiddleware(BaseHTTPMiddleware):
    """
    Measures processing duration, produces structured JSON access logs,
    and sets X-Process-Time-Ms and X-Correlation-ID response headers.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID") or f"corr_{uuid.uuid4().hex}"
        request.state.correlation_id = correlation_id

        start_time = time.perf_counter()

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Process-Time-Ms"] = str(duration_ms)

        cache_status = response.headers.get("X-Cache-Status", "NONE")

        logger.info(
            f"{request.method} {request.url.path} [{response.status_code}] cache={cache_status} in {duration_ms}ms",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "correlation_id": correlation_id,
                "duration_ms": duration_ms,
                "cache_status": cache_status,
            },
        )

        return response
