from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware

from talktodb_api_connector.api.routes import router as api_router
from talktodb_api_connector.config import Settings, load_settings
from talktodb_api_connector.database_registry import DatabaseConnectionRegistry

logger = logging.getLogger("talktodb_api_connector")


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
    settings: Settings | None = getattr(app.state, "settings", None)
    if settings is None:
        # Config was unreadable when the app object was built; re-raise with the
        # original message so uvicorn logs why startup failed.
        raise getattr(app.state, "settings_error", None) or RuntimeError(
            "Settings were not loaded."
        )
    DatabaseConnectionRegistry.initialize(settings)
    if settings.server.allowed_origins:
        logger.info(
            "CORS enabled for %d browser origin(s): %s",
            len(settings.server.allowed_origins),
            ", ".join(settings.server.allowed_origins),
        )
    else:
        logger.info("CORS off (server.allowed_origins is empty).")
    logger.info("Listening with database dialect %r.", settings.database.dialect)
    yield
    await DatabaseConnectionRegistry.close()


def create_app() -> FastAPI:
    """
    Build the ASGI app.

    Settings are read here rather than in ``lifespan`` because middleware must be
    attached before the application starts; Starlette rejects ``add_middleware``
    once the middleware stack is built.
    """
    _ensure_app_logging()
    app = FastAPI(title="talktodb-api-connector", lifespan=lifespan)

    settings: Settings | None
    try:
        settings = load_settings()
        app.state.settings = settings
        app.state.settings_error = None
    except (FileNotFoundError, OSError, ValueError) as exc:
        settings = None
        app.state.settings = None
        app.state.settings_error = exc
        logger.error("Could not load configuration: %s", exc)

    allowed_origins = list(settings.server.allowed_origins) if settings else []
    # CORS ingress: only attach middleware when origins are configured. With an
    # empty allowlist we skip CORSMiddleware so browsers receive no
    # Access-Control-Allow-Origin for cross-origin requests (same-origin still works).
    if allowed_origins:
        if "*" in allowed_origins:
            logger.warning(
                "server.allowed_origins contains '*': every website a user visits may call "
                "this API from their browser. List explicit origins instead."
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_router)

    @app.get("/")
    def root(request: Request) -> dict[str, str]:
        """Health-style root endpoint."""
        if allowed_origins:
            cors_note = (
                f"CORS enabled for {len(allowed_origins)} browser origin(s) "
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

    return app


app = create_app()
