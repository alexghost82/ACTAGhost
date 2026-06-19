"""FastAPI application exposing the ACTA cognitive pipeline + a web UI."""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from collections import defaultdict, deque
from pathlib import Path
import uuid
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException, Request, status as http_status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from acta.agents import AgentServices
from acta.channels import ChannelHub, TelegramChannel, WhatsAppChannel
from acta.config import get_settings
from acta.identity import IdentityRegistry, Role, User
from acta.logging_config import (
    bind_request_context,
    configure_logging,
    get_logger,
    reset_request_context,
    update_user_context,
)
from acta.observability import ObservabilityRuntime
from acta.orchestrator import Orchestrator
from acta.schemas import MemoryKind, Modality, UserRequest

log = get_logger("api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class ChatRequest(BaseModel):
    text: str = Field(..., description="User message")
    user_id: str | None = None
    modality: Modality = Modality.TEXT
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class CameraCreate(BaseModel):
    name: str = Field(..., description="Human-readable camera name")
    sensor_type: str = Field(default="rgb")
    width: int = Field(default=1280, ge=1)
    height: int = Field(default=720, ge=1)
    fps: int = Field(default=30, ge=1)
    source: str = Field(default="synthetic")
    id: str | None = None


class VisionAnalyzeRequest(BaseModel):
    camera_id: str | None = None
    instruction: str | None = None
    user_id: str | None = None
    # Inline frame analysis (used when no camera_id is supplied).
    sensor_type: str = Field(default="rgb")
    width: int | None = None
    height: int | None = None
    source: str | None = None
    persist: bool = True


DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100
MAX_OFFSET = 1000
MAX_AUDIT_SCAN = 2000


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before heavy processing (SEC-9)."""

    def __init__(self, app: Any, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        content_length = headers.get("content-length")
        if content_length and int(content_length) > self.max_body_size:
            response = JSONResponse({"detail": "request body too large"}, status_code=413)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class InMemoryRateLimitMiddleware:
    """Simple in-memory rate limiter for /api and /webhooks paths (SEC-9)."""

    def __init__(self, app: Any, per_minute: int) -> None:
        self.app = app
        self.per_minute = max(0, per_minute)
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or self.per_minute <= 0:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not (path.startswith("/api/") or path.startswith("/webhooks/")):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        key = f"{ip}:{headers.get('x-api-key', '')}:{path}"
        now = time.time()
        bucket = self.hits[key]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= self.per_minute:
            response = JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
            await response(scope, receive, send)
            return
        bucket.append(now)
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    services = AgentServices.build(settings)
    orchestrator = Orchestrator(services)
    # A5 observability: optional metrics/tracing/sentry wrappers.
    observability = ObservabilityRuntime(settings)
    observability.instrument_orchestrator(orchestrator)
    identity = IdentityRegistry.from_settings(settings)

    # Messaging channels (Telegram / WhatsApp).
    hub = ChannelHub(orchestrator)
    telegram = TelegramChannel(hub, settings)
    whatsapp = WhatsAppChannel(hub, settings)

    def _extract_credential(request: Request) -> str | None:
        auth = request.headers.get("Authorization", "")
        api_key = request.headers.get("X-API-Key")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        return bearer or api_key

    def _require_api_auth(request: Request) -> User:
        if not identity.auth_configured:
            principal = identity.default_session().principal
            update_user_context(principal.user_id)
            return principal
        session = identity.resolve(_extract_credential(request))
        if session is not None:
            update_user_context(session.principal.user_id)
            return session.principal
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    def _resolve_effective_user_id(principal: User, requested_user_id: str | None) -> str:
        if principal.role is Role.ADMIN:
            return requested_user_id or principal.user_id
        if requested_user_id and requested_user_id != principal.user_id:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="forbidden for requested user_id",
            )
        return principal.user_id

    def _normalize_pagination(limit: int, offset: int) -> tuple[int, int]:
        safe_limit = max(1, min(limit, MAX_PAGE_LIMIT))
        safe_offset = max(0, min(offset, MAX_OFFSET))
        return safe_limit, safe_offset

    def _build_chat_request(req: ChatRequest, principal: User) -> UserRequest:
        effective_user_id = _resolve_effective_user_id(principal, req.user_id)
        metadata = dict(req.metadata)
        metadata["principal_user_id"] = principal.user_id
        metadata["principal_role"] = principal.role.value
        return UserRequest(
            user_id=effective_user_id,
            text=req.text,
            modality=req.modality,
            metadata=metadata,
            attachments=req.attachments,
        )

    def _sse_event(event: str, payload: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _answer_chunks(answer: str, chunk_size: int = 80) -> Iterator[str]:
        text = answer or ""
        if not text:
            yield ""
            return
        for i in range(0, len(text), chunk_size):
            yield text[i:i + chunk_size]

    def _history_turn(record: dict[str, Any]) -> dict[str, Any]:
        content = str(record.get("content") or "")
        user_text = content
        assistant_text = ""
        if content.startswith("User: ") and "\nACTA:" in content:
            user_part, assistant_part = content.split("\nACTA:", 1)
            user_text = user_part.removeprefix("User: ").strip()
            assistant_text = assistant_part.strip()
        return {
            "id": record.get("id"),
            "request_id": (record.get("metadata") or {}).get("request_id"),
            "user_text": user_text,
            "assistant_text": assistant_text,
            "created_at": record.get("created_at"),
        }

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        observability.init_sentry()
        if not identity.auth_configured:
            log.warning("SEC-1/SEC-4: API authentication token is not configured; API remains unauthenticated")
        if not settings.whatsapp_app_secret:
            log.warning("SEC-3: WhatsApp app secret is not configured; webhook signature checks are disabled")
        # In long-poll mode (no webhook URL), run the Telegram poller in a thread.
        if telegram.enabled and not settings.telegram_webhook_url:
            telegram.start_polling()
            log.info("Telegram poller thread started")
        try:
            yield
        finally:
            telegram.stop()
            stop_hub = getattr(hub, "stop", None)
            if callable(stop_hub):
                stop_hub()
            stop_orchestrator = getattr(orchestrator, "shutdown", None)
            if callable(stop_orchestrator):
                stop_orchestrator()

    app = FastAPI(
        title="ACTA GHOST — Autonomous Cognitive Task Assistant",
        version="0.1.0",
        description="Agentic, multi-model, memory-driven personal cognitive platform.",
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.orchestrator = orchestrator
    app.state.hub = hub
    app.state.telegram = telegram
    app.state.whatsapp = whatsapp
    app.state.observability = observability
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Hub-Signature-256"],
    )
    app.add_middleware(BodySizeLimitMiddleware, max_body_size=settings.api_max_body_size_bytes)
    app.add_middleware(InMemoryRateLimitMiddleware, per_minute=settings.api_rate_limit_per_minute)

    # A5 observability: request context + request_id propagation.
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        tokens = bind_request_context(request_id=request_id)
        started = time.perf_counter()
        status_code = 500
        response = None
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            raise
        finally:
            elapsed = time.perf_counter() - started
            observability.observe_http(request.method, request.url.path, status_code, elapsed)
            log.info("http_request method=%s path=%s status=%s", request.method, request.url.path, status_code)
            reset_request_context(tokens)
        if response is None:
            return JSONResponse({"detail": "internal server error"}, status_code=500)
        response.headers["X-Request-ID"] = request_id
        return response

    # -- API routes -------------------------------------------------------- #
    @app.get("/api/v1/health")
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "app": settings.app_name, "version": app.version}

    @app.get("/metrics")
    def metrics() -> PlainTextResponse:
        # A5 observability choice: metrics endpoint is intentionally unauthenticated.
        status_code, payload, content_type = observability.metrics_payload()
        return PlainTextResponse(payload.decode("utf-8"), status_code=status_code, media_type=content_type)

    @app.get("/api/v1/ready")
    @app.get("/api/ready")
    def ready() -> JSONResponse:
        checks: dict[str, bool] = {}
        try:
            checks["services"] = app.state.services is services
            checks["orchestrator"] = app.state.orchestrator is orchestrator
            checks["router"] = bool(services.router.available_providers())
            services.memory.stats()
            checks["memory"] = True
        except Exception:  # pragma: no cover - defensive readiness guard
            checks["memory"] = False
        healthy = all(checks.values()) if checks else False
        status_code = 200 if healthy else 503
        status_text = "ready" if healthy else "not_ready"
        return JSONResponse(
            {
                "status": status_text,
                "app": settings.app_name,
                "version": app.version,
                "checks": checks,
            },
            status_code=status_code,
        )

    # API maturity: `/api/v1/*` is the canonical surface; `/api/*` stays as a
    # backward-compatible alias for existing clients/tests.
    @app.get("/api/v1/status")
    @app.get("/api/status")
    def status(_: User = Depends(_require_api_auth)) -> dict[str, Any]:
        return {
            "providers": services.router.available_providers(),
            "default_provider": settings.default_provider,
            "memory": services.memory.stats(),
            "knowledge_graph": services.kg.stats(),
            "connectors": services.connectors.names(),
        }

    @app.get("/api/v1/agents")
    @app.get("/api/agents")
    def agents(_: User = Depends(_require_api_auth)) -> dict[str, Any]:
        core = [
            orchestrator.multimodal,
            orchestrator.intent,
            orchestrator.context,
            orchestrator.reasoning,
            orchestrator.planning,
            orchestrator.decision,
            orchestrator.memory,
            orchestrator.kg,
            orchestrator.integration,
            orchestrator.security,
            orchestrator.ui,
        ]
        listed = [
            {"name": a.NAME, "sub_prompt": a.SUB_PROMPT, "capabilities": sorted(services.permissions.capabilities(a.NAME))}
            for a in core
        ]
        listed.append(
            {"name": "orchestrator", "sub_prompt": Orchestrator.SUB_PROMPT,
             "capabilities": sorted(services.permissions.capabilities("orchestrator"))}
        )
        for name, worker in orchestrator.workers.items():
            listed.append(
                {"name": name, "sub_prompt": worker.SUB_PROMPT, "role": "worker",
                 "capabilities": sorted(services.permissions.capabilities(name))}
            )
        return {"agents": listed, "count": len(listed)}

    @app.post("/api/v1/chat")
    @app.post("/api/chat")
    def chat(req: ChatRequest, principal: User = Depends(_require_api_auth)) -> JSONResponse:
        request = _build_chat_request(req, principal)
        response = orchestrator.run(request)
        return JSONResponse(response.model_dump())

    @app.post("/api/v1/chat/stream")
    @app.post("/api/chat/stream")
    def stream_chat_post(req: ChatRequest, principal: User = Depends(_require_api_auth)) -> StreamingResponse:
        request = _build_chat_request(req, principal)

        def event_stream() -> Iterator[str]:
            yield _sse_event("meta", {"request_id": request.request_id, "version": "v1"})
            response = orchestrator.run(request)
            for trace_entry in response.trace:
                yield _sse_event("trace", trace_entry.model_dump())
            for chunk in _answer_chunks(response.answer):
                yield _sse_event("answer_delta", {"delta": chunk})
            yield _sse_event("complete", response.model_dump())

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.get("/api/v1/chat/stream")
    @app.get("/api/chat/stream")
    def stream_chat_get(
        text: str,
        user_id: str | None = None,
        language: str | None = None,
        principal: User = Depends(_require_api_auth),
    ) -> StreamingResponse:
        req = ChatRequest(text=text, user_id=user_id, metadata={"language": language} if language else {})
        return stream_chat_post(req, principal)

    @app.get("/api/v1/memory")
    @app.get("/api/memory")
    def memory(
        kind: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        principal: User = Depends(_require_api_auth),
    ) -> dict[str, Any]:
        effective_user_id = _resolve_effective_user_id(principal, user_id)
        limit, offset = _normalize_pagination(limit, offset)
        mem_kind = MemoryKind(kind) if kind else None
        scan_limit = min(limit + offset, MAX_OFFSET + MAX_PAGE_LIMIT)
        records = services.memory.recent(mem_kind, user_id=effective_user_id, limit=scan_limit)
        paged_records = records[offset:offset + limit]
        stats = services.memory.stats(user_id=effective_user_id)
        total = stats.get(mem_kind.value, 0) if mem_kind else sum(stats.values())
        return {
            "records": [r.to_dict() for r in paged_records],
            "stats": stats,
            "limit": limit,
            "offset": offset,
            "total": total,
            "has_more": offset + len(paged_records) < total,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(paged_records),
                "total": total,
                "has_more": offset + len(paged_records) < total,
            },
        }

    @app.get("/api/v1/audit")
    @app.get("/api/audit")
    def audit(
        limit: int = 50,
        offset: int = 0,
        user_id: str | None = None,
        principal: User = Depends(_require_api_auth),
    ) -> dict[str, Any]:
        effective_user_id = _resolve_effective_user_id(principal, user_id)
        limit, offset = _normalize_pagination(limit, offset)
        scan_limit = min(limit + offset, MAX_AUDIT_SCAN)
        scoped = []
        for entry in services.audit.tail(scan_limit):
            details = entry.get("details") if isinstance(entry, dict) else None
            entry_user_id = details.get("user_id") if isinstance(details, dict) else None
            if entry_user_id == effective_user_id:
                scoped.append(entry)
        paged_entries = scoped[offset:offset + limit]
        total = len(scoped)
        return {
            "entries": paged_entries,
            "limit": limit,
            "offset": offset,
            "total": total,
            "has_more": offset + len(paged_entries) < total,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(paged_entries),
                "total": total,
                "has_more": offset + len(paged_entries) < total,
            },
        }

    @app.get("/api/v1/history")
    @app.get("/api/history")
    def history(
        user_id: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
        principal: User = Depends(_require_api_auth),
    ) -> dict[str, Any]:
        effective_user_id = _resolve_effective_user_id(principal, user_id)
        limit, offset = _normalize_pagination(limit, offset)
        scan_limit = min(limit + offset, MAX_OFFSET + MAX_PAGE_LIMIT)
        records = services.memory.recent(MemoryKind.EPISODIC, user_id=effective_user_id, limit=scan_limit)
        turns = [_history_turn(record.to_dict()) for record in records]
        paged_turns = turns[offset:offset + limit]
        total = services.memory.stats(user_id=effective_user_id).get(MemoryKind.EPISODIC.value, 0)
        return {
            "items": paged_turns,
            "limit": limit,
            "offset": offset,
            "total": total,
            "has_more": offset + len(paged_turns) < total,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(paged_turns),
                "total": total,
                "has_more": offset + len(paged_turns) < total,
            },
        }

    @app.get("/api/v1/channels")
    @app.get("/api/channels")
    def channels(_: User = Depends(_require_api_auth)) -> dict[str, Any]:
        return {
            "telegram": {
                "enabled": telegram.enabled,
                "mode": "webhook" if settings.telegram_webhook_url else "polling",
            },
            "whatsapp": {"enabled": whatsapp.enabled},
        }

    # -- AGENT: Vision / cameras ------------------------------------------- #
    @app.get("/api/v1/vision/status")
    @app.get("/api/vision/status")
    def vision_status(_: User = Depends(_require_api_auth)) -> dict[str, Any]:
        vision = services.vision
        return {
            "enabled": vision.enabled,
            "vlm_provider": settings.vlm_provider,
            "quantization": settings.vlm_quantization,
            "patch_size": settings.vision_patch_size,
            "max_patches": settings.vision_max_patches,
            "pixel_shuffle_scale": settings.vision_pixel_shuffle_scale,
            "lora_enabled": settings.vision_lora_enabled,
            "lora_adapters": vision.lora.names(),
            "cameras": len(vision.cameras.list()),
        }

    @app.get("/api/v1/cameras")
    @app.get("/api/cameras")
    def list_cameras(_: User = Depends(_require_api_auth)) -> dict[str, Any]:
        cams = [c.to_dict() for c in services.vision.cameras.list()]
        return {"cameras": cams, "count": len(cams)}

    @app.post("/api/v1/cameras")
    @app.post("/api/cameras")
    def register_camera(
        req: CameraCreate, principal: User = Depends(_require_api_auth)
    ) -> JSONResponse:
        try:
            spec = services.vision.cameras.add(
                req.name,
                sensor_type=req.sensor_type,
                width=req.width,
                height=req.height,
                fps=req.fps,
                source=req.source,
                camera_id=req.id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        services.audit.record(
            "integration", "camera_registered", camera_id=spec.id, user_id=principal.user_id
        )
        return JSONResponse({"ok": True, "camera": spec.to_dict()})

    @app.delete("/api/v1/cameras/{camera_id}")
    @app.delete("/api/cameras/{camera_id}")
    def delete_camera(camera_id: str, _: User = Depends(_require_api_auth)) -> dict[str, Any]:
        removed = services.vision.cameras.remove(camera_id)
        if not removed:
            raise HTTPException(status_code=404, detail="camera not found")
        return {"ok": True}

    @app.post("/api/v1/vision/analyze")
    @app.post("/api/vision/analyze")
    def vision_analyze(
        req: VisionAnalyzeRequest, principal: User = Depends(_require_api_auth)
    ) -> JSONResponse:
        if not services.vision.enabled:
            raise HTTPException(status_code=409, detail="vision subsystem is disabled")
        effective_user_id = _resolve_effective_user_id(principal, req.user_id)
        try:
            if req.camera_id:
                analysis = services.vision.capture_and_analyze(
                    req.camera_id,
                    req.instruction,
                    user_id=effective_user_id,
                    agent="integration",
                    persist=req.persist,
                )
            elif req.width and req.height:
                from acta.schemas import SensorType
                from acta.vision.frames import VisionFrame

                frame = VisionFrame(
                    width=req.width,
                    height=req.height,
                    sensor_type=SensorType(req.sensor_type),
                    camera_id="api-frame",
                    source=req.source,
                    metadata={"path": req.source} if req.source else {},
                )
                analysis = services.vision.pipeline.analyze_frame(
                    frame,
                    req.instruction,
                    user_id=effective_user_id,
                    agent="integration",
                    persist=req.persist,
                )
            else:
                raise HTTPException(
                    status_code=422, detail="provide camera_id or inline width+height"
                )
        except KeyError:
            raise HTTPException(status_code=404, detail="camera not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse({"ok": True, "analysis": analysis.to_dict()})

    # -- Telegram webhook (alternative to polling) ------------------------- #
    @app.post("/webhooks/telegram")
    async def telegram_webhook(request: Request, _: User = Depends(_require_api_auth)) -> dict[str, Any]:
        update = await request.json()
        telegram.handle_update(update)
        return {"ok": True}

    # -- WhatsApp webhook (Meta Cloud API) --------------------------------- #
    @app.get("/webhooks/whatsapp")
    def whatsapp_verify(request: Request) -> PlainTextResponse:
        params = request.query_params
        challenge = whatsapp.verify(
            params.get("hub.mode"),
            params.get("hub.verify_token"),
            params.get("hub.challenge"),
        )
        if challenge is not None:
            return PlainTextResponse(challenge)
        return PlainTextResponse("forbidden", status_code=403)

    @app.post("/webhooks/whatsapp")
    async def whatsapp_webhook(request: Request, _: User = Depends(_require_api_auth)) -> dict[str, Any]:
        raw = await request.body()
        if settings.whatsapp_app_secret:
            signature = request.headers.get("X-Hub-Signature-256")
            if not whatsapp.verify_signature(raw, signature):
                # Preserve explicit JSON 403 response for webhook callers.
                return JSONResponse({"detail": "invalid webhook signature"}, status_code=403)  # type: ignore[return-value]
        payload = await request.json()
        count = whatsapp.handle_webhook(payload)
        return {"ok": True, "processed": count}

    # -- Web UI ------------------------------------------------------------ #
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    return app


app = create_app()


def run() -> None:
    """Console entrypoint: ``acta`` (see pyproject [project.scripts])."""
    import uvicorn

    settings = get_settings()
    uvicorn.run("acta.api.app:app", host="127.0.0.1", port=8765, log_level=settings.log_level.lower())


if __name__ == "__main__":
    run()
