"""Configuration: JSON file at ~/.talktodb.config — ``server``, ``database``, and ``service_enabled``."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ServerConfig(BaseModel):
    """HTTP API-related settings (CORS and bind port used by the CLI)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    port: int = Field(default=2026, description="HTTP API bind port for uvicorn (CLI).")
    allowed_origins: list[str] = Field(
        default_factory=list,
        description=(
            "Browser Origin values allowed by CORS (e.g. https://app.example.com). "
            "Not used for database access control."
        ),
    )


class DatabaseConfig(BaseModel):
    """Database connection and query policy."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dialect: str = "postgresql"
    host: str = "127.0.0.1"
    port: int = 5432
    username: str = ""
    password: str = ""
    database: str = ""
    read_only: bool = False


class Settings(BaseModel):
    """Root settings: nested ``server`` and ``database`` matching the on-disk JSON shape."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    service_enabled: bool = Field(
        default=False,
        description="True when a Windows service was registered via ``talktodb service install``.",
    )


def _config_path() -> Path:
    return Path.home() / ".talktodb.config"


def load_settings(path: Path | None = None) -> Settings:
    """
    Load settings from a JSON config file only (no environment variables).

    The file defaults to ``~/.talktodb.config``. It must be a JSON object with
    ``server`` and ``database`` objects as defined by :class:`Settings`.
    If the file does not exist, a :exc:`FileNotFoundError` is raised.
    """
    cfg_path = path or _config_path()
    if not cfg_path.is_file():
        raise FileNotFoundError(
            f"TalkToDB API connector config not found: {cfg_path}. "
            "Create this file with JSON (use `talktodb config set` to edit, or "
            "author it manually). See the Settings model for valid keys."
        )
    try:
        raw_text = cfg_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(
            f"Could not read TalkToDB API connector config file: {cfg_path}"
        ) from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in TalkToDB API connector config {cfg_path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            f"TalkToDB API connector config {cfg_path} must be a JSON object, not "
            f"{type(data).__name__}."
        )

    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        missing = [
            err["loc"][0]
            for err in exc.errors()
            if err["type"] == "missing" and err["loc"]
        ]
        if missing:
            fields = ", ".join(str(m) for m in missing)
            raise ValueError(
                f"TalkToDB API connector config {cfg_path} is missing required field(s): {fields}. "
                "Set them in the JSON file (defaults apply for omitted keys when validation allows)."
            ) from exc
        raise ValueError(
            f"Invalid TalkToDB API connector config in {cfg_path}:\n{exc}"
        ) from exc
