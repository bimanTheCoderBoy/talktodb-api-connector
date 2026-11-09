"""talktodb-api-connector: configurable SQL API (skeleton)."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from talktodb_api_connector.api.main import app

__all__ = ["app"]


def __getattr__(name: str) -> Any:
    """Load the ASGI app on demand so CLI commands do not read config at import."""
    if name == "app":
        from talktodb_api_connector.api.main import app

        return app
    raise AttributeError(f"module {__name__} has no attribute {name}")
