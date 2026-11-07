from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware

from talktodb_api_connector.api.routes import router as api_router
from talktodb_api_connector.config import load_settings
from talktodb_api_connector.database_registry import DatabaseConnectionRegistry


def _ensure_app_logging() -> None:
    """Configure root logging to stderr when nothing else (e.g. uvicorn) configured it."""
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_app_logging()
    settings = load_settings()
    app.state.settings = settings
    DatabaseConnectionRegistry.initialize(settings)
    # CORS ingress: only attach middleware when origins are configured. With an
    # empty allowlist we skip CORSMiddleware so browsers receive no
    # Access-Control-Allow-Origin for cross-origin requests (same-origin still works).
    if settings.server.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.server.allowed_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    yield
    await DatabaseConnectionRegistry.close()


app = FastAPI(title="talktodb-api-connector", lifespan=lifespan)
app.include_router(api_router)


@app.get("/")
def root(request: Request) -> dict[str, str]:
    """Health-style root endpoint."""
    settings = request.app.state.settings
    if settings.server.allowed_origins:
        cors_note = (
            f"CORS enabled for {len(settings.server.allowed_origins)} browser origin(s) "
            "(not database access control)"
        )
    else:
        cors_note = (
            "CORS off (allowed_origins empty; no CORSMiddleware — "
            "cross-origin browser requests blocked)"
        )
    return {
        "status": "ok",
        "message": "talktodb-api-connector",
        "bind_host": "127.0.0.1",
        "settings": ("loaded from ~/.talktodb.config (JSON only)"),
        "cors": cors_note,
    }
