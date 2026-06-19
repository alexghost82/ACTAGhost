"""FastAPI application exposing the ACTA cognitive pipeline + a web UI."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from acta.agents import AgentServices
from acta.channels import ChannelHub, TelegramChannel, WhatsAppChannel
from acta.config import get_settings
from acta.logging_config import configure_logging, get_logger
from acta.orchestrator import Orchestrator
from acta.schemas import MemoryKind, Modality, UserRequest

log = get_logger("api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class ChatRequest(BaseModel):
    text: str = Field(..., description="User message")
    user_id: str = "default"
    modality: Modality = Modality.TEXT
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    services = AgentServices.build(settings)
    orchestrator = Orchestrator(services)

    app = FastAPI(
        title="ACTA GHOST — Autonomous Cognitive Task Assistant",
        version="0.1.0",
        description="Agentic, multi-model, memory-driven personal cognitive platform.",
    )
    app.state.services = services
    app.state.orchestrator = orchestrator

    # Messaging channels (Telegram / WhatsApp).
    hub = ChannelHub(orchestrator)
    telegram = TelegramChannel(hub, settings)
    whatsapp = WhatsAppChannel(hub, settings)
    app.state.hub = hub
    app.state.telegram = telegram
    app.state.whatsapp = whatsapp

    @app.on_event("startup")
    def _start_channels() -> None:
        # In long-poll mode (no webhook URL), run the Telegram poller in a thread.
        if telegram.enabled and not settings.telegram_webhook_url:
            t = threading.Thread(target=telegram.poll_forever, daemon=True, name="tg-poller")
            t.start()
            log.info("Telegram poller thread started")

    # -- API routes -------------------------------------------------------- #
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "app": settings.app_name, "version": app.version}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {
            "providers": services.router.available_providers(),
            "default_provider": settings.default_provider,
            "memory": services.memory.stats(),
            "knowledge_graph": services.kg.stats(),
            "connectors": services.connectors.names(),
        }

    @app.get("/api/agents")
    def agents() -> dict[str, Any]:
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
    def chat(req: ChatRequest) -> JSONResponse:
        request = UserRequest(
            user_id=req.user_id,
            text=req.text,
            modality=req.modality,
            metadata=req.metadata,
            attachments=req.attachments,
        )
        response = orchestrator.run(request)
        return JSONResponse(response.model_dump())

    @app.get("/api/memory")
    def memory(kind: str | None = None, user_id: str = "default", limit: int = 20) -> dict[str, Any]:
        mem_kind = MemoryKind(kind) if kind else None
        records = services.memory.recent(mem_kind, user_id=user_id, limit=limit)
        return {"records": [r.to_dict() for r in records], "stats": services.memory.stats(user_id=user_id)}

    @app.get("/api/audit")
    def audit(limit: int = 50) -> dict[str, Any]:
        return {"entries": services.audit.tail(limit)}

    @app.get("/api/channels")
    def channels() -> dict[str, Any]:
        return {
            "telegram": {
                "enabled": telegram.enabled,
                "mode": "webhook" if settings.telegram_webhook_url else "polling",
            },
            "whatsapp": {"enabled": whatsapp.enabled},
        }

    # -- Telegram webhook (alternative to polling) ------------------------- #
    @app.post("/webhooks/telegram")
    async def telegram_webhook(request: Request) -> dict[str, Any]:
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
    async def whatsapp_webhook(request: Request) -> dict[str, Any]:
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
