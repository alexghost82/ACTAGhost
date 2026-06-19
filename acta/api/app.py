"""FastAPI application exposing the ACTA cognitive pipeline + a web UI."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status as http_status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from acta.agents import AgentServices
from acta.channels import ChannelHub, TelegramChannel, WhatsAppChannel
from acta.config import get_settings
from acta.identity import IdentityRegistry, Role, User
from acta.logging_config import configure_logging, get_logger
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


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before heavy processing (SEC-9)."""

    def __init__(self, app: Any, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
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

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
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
    configure_logging(settings.log_level)
    services = AgentServices.build(settings)
    orchestrator = Orchestrator(services)
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
            return identity.default_session().principal
        session = identity.resolve(_extract_credential(request))
        if session is not None:
            return session.principal
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Hub-Signature-256"],
    )
    app.add_middleware(BodySizeLimitMiddleware, max_body_size=settings.api_max_body_size_bytes)
    app.add_middleware(InMemoryRateLimitMiddleware, per_minute=settings.api_rate_limit_per_minute)

    # -- API routes -------------------------------------------------------- #
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "app": settings.app_name, "version": app.version}

    @app.get("/api/status")
    def status(_: User = Depends(_require_api_auth)) -> dict[str, Any]:
        return {
            "providers": services.router.available_providers(),
            "default_provider": settings.default_provider,
            "memory": services.memory.stats(),
            "knowledge_graph": services.kg.stats(),
            "connectors": services.connectors.names(),
        }

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

    @app.post("/api/chat")
    def chat(req: ChatRequest, principal: User = Depends(_require_api_auth)) -> JSONResponse:
        # Identity rule: non-admin principals are always scoped to their own user_id.
        if principal.role is Role.ADMIN:
            effective_user_id = req.user_id or principal.user_id
        else:
            if req.user_id and req.user_id != principal.user_id:
                raise HTTPException(
                    status_code=http_status.HTTP_403_FORBIDDEN,
                    detail="forbidden for requested user_id",
                )
            effective_user_id = principal.user_id
        metadata = dict(req.metadata)
        metadata["principal_user_id"] = principal.user_id
        metadata["principal_role"] = principal.role.value
        request = UserRequest(
            user_id=effective_user_id,
            text=req.text,
            modality=req.modality,
            metadata=metadata,
            attachments=req.attachments,
        )
        response = orchestrator.run(request)
        return JSONResponse(response.model_dump())

    @app.get("/api/memory")
    def memory(
        kind: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
        principal: User = Depends(_require_api_auth),
    ) -> dict[str, Any]:
        if principal.role is Role.ADMIN:
            effective_user_id = user_id or principal.user_id
        else:
            if user_id and user_id != principal.user_id:
                raise HTTPException(
                    status_code=http_status.HTTP_403_FORBIDDEN,
                    detail="forbidden for requested user_id",
                )
            effective_user_id = principal.user_id
        mem_kind = MemoryKind(kind) if kind else None
        records = services.memory.recent(mem_kind, user_id=effective_user_id, limit=limit)
        return {
            "records": [r.to_dict() for r in records],
            "stats": services.memory.stats(user_id=effective_user_id),
        }

    @app.get("/api/audit")
    def audit(
        limit: int = 50,
        user_id: str | None = None,
        principal: User = Depends(_require_api_auth),
    ) -> dict[str, Any]:
        if principal.role is Role.ADMIN:
            effective_user_id = user_id or principal.user_id
        else:
            if user_id and user_id != principal.user_id:
                raise HTTPException(
                    status_code=http_status.HTTP_403_FORBIDDEN,
                    detail="forbidden for requested user_id",
                )
            effective_user_id = principal.user_id
        scoped = []
        for entry in services.audit.tail(limit):
            details = entry.get("details") if isinstance(entry, dict) else None
            entry_user_id = details.get("user_id") if isinstance(details, dict) else None
            if entry_user_id == effective_user_id:
                scoped.append(entry)
        return {"entries": scoped}

    @app.get("/api/channels")
    def channels(_: User = Depends(_require_api_auth)) -> dict[str, Any]:
        return {
            "telegram": {
                "enabled": telegram.enabled,
                "mode": "webhook" if settings.telegram_webhook_url else "polling",
            },
            "whatsapp": {"enabled": whatsapp.enabled},
        }

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
                return JSONResponse({"detail": "invalid webhook signature"}, status_code=403)
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
