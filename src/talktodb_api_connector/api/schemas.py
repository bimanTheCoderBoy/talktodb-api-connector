"""API response models aligned with the agent server tool contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ServerConfigPublic(BaseModel):
    """Server subsection returned by ``GET /settings``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    port: int = Field(default=2026, description="HTTP API bind port (uvicorn; default 2026).")
    allowed_origins: list[str] = Field(
        default_factory=list,
        description="CORS allowlisted browser origins.",
    )


class DatabaseConfigPublic(BaseModel):
    """Database subsection returned by ``GET /settings`` (password never included)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dialect: str
    host: str = Field(default="127.0.0.1", description="Database server hostname (SQLAlchemy URL).")
    port: int = Field(default=5432, description="Database server port (SQLAlchemy URL).")
    username: str
    database: str
    read_only: bool = Field(default=False, description="When true, only read-style SQL is allowed.")


class SettingsPublic(BaseModel):
    """Configuration returned by ``GET /settings`` (nested shape; database password omitted)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    server: ServerConfigPublic
    database: DatabaseConfigPublic


class RunDbQueryRequest(BaseModel):
    """Body for ``POST /query`` — raw SQL and optional bind parameters."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sql: str = Field(..., description="SQL statement to execute.")
    params: dict[str, Any] | None = Field(
        default=None,
        description="Optional named parameters for bound placeholders in ``sql``.",
    )


class RunDbQueryOutput(BaseModel):
    """Structured result for ``run_db_query``-style tooling (matches agent_server shape)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    success: bool = Field(..., description="Whether the query completed successfully.")
    error: str | None = Field(
        default=None,
        description="Error message when ``success`` is false.",
    )
    columns: list[str] | None = Field(
        default=None,
        description="Column names in order for row-returning queries.",
    )
    rows: list[list[Any]] | None = Field(
        default=None,
        description="Row values aligned with ``columns`` (list of lists).",
    )
    row_count: int | None = Field(
        default=None,
        description="Number of rows returned or affected, depending on query type.",
    )
