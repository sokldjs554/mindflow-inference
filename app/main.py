import asyncio
import contextlib
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from app.api import router
from app.config import settings
from app.db import SessionFactory, engine
from app.errors import DomainError
from app.models import InferenceJob
from app.observability import HTTP_LATENCY, WS_CONNECTIONS, log
from app.queue import event_stream, redis_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.redis = redis_client()
    yield
    await app.state.redis.aclose()
    await engine.dispose()


app = FastAPI(
    title="Mindflow Inference",
    version="0.1.0",
    lifespan=lifespan,
    description="Evidence-grounded documentation infrastructure. Synthetic mock mode. "
    "Not a diagnostic, treatment, or clinical decision system.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=[
        "Content-Type",
        "X-API-Key",
        "Idempotency-Key",
        "X-Request-ID",
        "X-Correlation-ID",
    ],
)
app.include_router(router)
static = Path(__file__).parent / "static"
static.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static), name="static")


def error(request: Request, code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": code,
                "message": message,
                "request_id": getattr(request.state, "request_id", "unknown"),
            }
        },
        status_code=status,
    )


def safe_id(value: str | None) -> str:
    try:
        return str(uuid.UUID(value or ""))
    except ValueError:
        return str(uuid.uuid4())


@app.middleware("http")
async def boundary(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request.state.request_id = safe_id(request.headers.get("x-request-id"))
    request.state.correlation_id = safe_id(request.headers.get("x-correlation-id"))
    start = time.perf_counter()
    try:
        if request.url.path.startswith("/api") or request.url.path == "/metrics":
            if settings().api_key and not secrets.compare_digest(
                request.headers.get("x-api-key", ""), settings().api_key
            ):
                raise DomainError("UNAUTHORIZED", "Valid API key required", 401)
        if request.url.path.startswith("/api"):
            client = request.client.host if request.client else "unknown"
            key = f"{settings().stream_prefix}:rate:{client}:{int(time.time()) // 60}"
            count = await request.app.state.redis.eval(
                "local n=redis.call('INCR',KEYS[1]); "
                "if n==1 then redis.call('EXPIRE',KEYS[1],61) end; return n",
                1,
                key,
            )
            if count > settings().rate_limit:
                raise DomainError(
                    "RATE_LIMITED", "Request limit exceeded; retry in one minute", 429
                )
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > settings().max_body_bytes:
                raise DomainError("BODY_TOO_LARGE", "Request exceeds body size limit", 413)
        request._body = bytes(body)
        response = await call_next(request)
    except DomainError as exc:
        response = error(request, exc.code, exc.message, exc.status)
    except Exception:
        log(
            "request_failed",
            request_id=request.state.request_id,
            correlation_id=request.state.correlation_id,
            code="DEPENDENCY_UNAVAILABLE",
        )
        response = error(request, "DEPENDENCY_UNAVAILABLE", "Service temporarily unavailable", 503)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'"
        )
        if request.url.path == "/"
        else "frame-ancestors 'none'"
    )
    route = getattr(request.scope.get("route"), "path", "unmatched")
    HTTP_LATENCY.labels(request.method, route, str(response.status_code)).observe(
        time.perf_counter() - start
    )
    return response


@app.exception_handler(DomainError)
async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
    return error(request, exc.code, exc.message, exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error(request, "INVALID_REQUEST", "Request does not match the API schema", 422)


@app.exception_handler(IntegrityError)
async def constraint_error(request: Request, exc: IntegrityError) -> JSONResponse:
    return error(request, "CONSTRAINT_CONFLICT", "Request conflicts with existing data", 409)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return error(request, "HTTP_ERROR", "Requested operation is unavailable", exc.status_code)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static / "index.html")


@app.get("/health", tags=["Operations"])
async def health() -> dict[str, str]:
    return {"status": "alive", "provider_mode": "mock"}


@app.get("/ready", tags=["Operations"])
async def ready(request: Request) -> Response:
    try:
        async with asyncio.timeout(2):
            async with SessionFactory() as db:
                await db.execute(text("SELECT 1 FROM alembic_version LIMIT 1"))
            await request.app.state.redis.ping()
        return JSONResponse({"status": "ready", "postgres": True, "redis": True})
    except Exception:
        return error(request, "NOT_READY", "PostgreSQL or Redis is unavailable", 503)


@app.get("/metrics", tags=["Operations"])
async def metrics() -> Response:
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


@app.websocket("/ws/jobs/{job_id}")
async def websocket(websocket: WebSocket, job_id: uuid.UUID) -> None:
    origin = websocket.headers.get("origin")
    if origin and origin not in settings().cors_origins:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    if settings().api_key:
        try:
            auth = await asyncio.wait_for(websocket.receive_json(), timeout=5)
            if not secrets.compare_digest(str(auth.get("api_key", "")), settings().api_key):
                await websocket.close(code=1008)
                return
        except (TimeoutError, ValueError, WebSocketDisconnect):
            await websocket.close(code=1008)
            return
    redis = websocket.app.state.redis
    # Capture stream cursor BEFORE DB snapshot so a transition cannot fall into a subscription gap.
    stream = event_stream(job_id)
    latest = await redis.xrevrange(stream, count=1)
    cursor = latest[0][0] if latest else "0-0"
    async with SessionFactory() as db:
        job = await db.get(InferenceJob, job_id)
        if job is None:
            await websocket.close(code=1008)
            return
        initial = {
            "job_id": str(job.id),
            "state": job.state,
            "progress": job.progress,
            "type": "snapshot",
        }
    WS_CONNECTIONS.inc()

    async def send_events() -> None:
        nonlocal cursor
        await websocket.send_json(initial)
        while True:
            rows = await redis.xread({stream: cursor}, count=100, block=10_000)
            if not rows:
                await websocket.send_json({"type": "heartbeat", "job_id": str(job_id)})
            for _, messages in rows:
                for message_id, fields in messages:
                    cursor = message_id
                    await websocket.send_json(
                        {
                            **fields,
                            "progress": int(fields["progress"]),
                            "type": "progress",
                            "cursor": cursor,
                        }
                    )

    async def receive_disconnect() -> None:
        while True:
            await websocket.receive_text()

    tasks = [asyncio.create_task(send_events()), asyncio.create_task(receive_disconnect())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        WS_CONNECTIONS.dec()
