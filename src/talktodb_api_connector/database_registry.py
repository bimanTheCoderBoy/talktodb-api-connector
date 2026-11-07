"""Singleton registry for the shared async SQLAlchemy engine and session factory."""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.engine.url import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from talktodb_api_connector.config import Settings


def _async_drivername(dialect: str) -> str:
    """Normalize ``DatabaseConfig.dialect`` to an async ``dialect+driver`` SQLAlchemy URL prefix."""
    d = dialect.strip().lower()
    if "+" in d:
        return d
    return {
        "postgresql": "postgresql+asyncpg",
        "postgres": "postgresql+asyncpg",
        "mysql": "mysql+aiomysql",
        "mariadb": "mysql+aiomysql",
        "sqlite": "sqlite+aiosqlite",
        "mssql": "mssql+aioodbc",
        "sqlserver": "mssql+aioodbc",
        "oracle": "oracle+oracledb",
    }.get(d, d)


def settings_to_database_url(settings: Settings) -> URL:
    """
    Build an async SQLAlchemy URL from :class:`Settings` (uses :attr:`Settings.database`).

    If ``settings.database.dialect`` already contains ``+`` (e.g. ``postgresql+asyncpg``), it is
    used as-is; otherwise common names are mapped to async drivers (e.g. ``postgres``
    -> ``postgresql+asyncpg``). Install the matching async DBAPI for your backend.
    """
    db = settings.database
    drivername = _async_drivername(db.dialect)
    if drivername.startswith("sqlite"):
        return URL.create(
            drivername=drivername,
            database=db.database,
        )
    return URL.create(
        drivername=drivername,
        username=db.username,
        password=db.password,
        host=db.host,
        port=db.port,
        database=db.database,
    )


class DatabaseConnectionRegistry:
    """
    Process-wide singleton: one :class:`AsyncEngine` and one ``async_sessionmaker``.

    Call :meth:`initialize` once with :class:`Settings`, then use :meth:`get_session`
    or :meth:`get_session_context`. Engine creation is lazy on first session access,
    guarded by :class:`threading.Lock` (double-checked) so concurrent first access does
    not build duplicate engines.
    """

    _lock = threading.Lock()
    _settings: Settings | None = None
    _engine: AsyncEngine | None = None
    _session_maker: async_sessionmaker[AsyncSession] | None = None

    @classmethod
    def initialize(cls, settings: Settings) -> None:
        """Store ``settings`` for later lazy engine creation. Does not connect or pool."""
        cls._settings = settings

    @classmethod
    def _ensure_engine(cls) -> None:
        """Create engine and session maker once from :attr:`_settings`."""
        if cls._settings is None:
            raise RuntimeError(
                "DatabaseConnectionRegistry.initialize(settings) must be called before "
                "using get_session()."
            )
        if cls._engine is not None and cls._session_maker is not None:
            return
        with cls._lock:
            if cls._engine is not None and cls._session_maker is not None:
                return
            url = settings_to_database_url(cls._settings)
            engine = create_async_engine(
                url,
                pool_size=2,
                max_overflow=0,
            )
            session_maker = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            cls._engine = engine
            cls._session_maker = session_maker

    @classmethod
    def get_session(cls) -> AsyncSession:
        """Return a new :class:`AsyncSession`, creating the shared engine on first use."""
        cls._ensure_engine()
        assert cls._session_maker is not None
        return cls._session_maker()

    @classmethod
    @asynccontextmanager
    async def get_session_context(cls) -> AsyncIterator[AsyncSession]:
        """Async context manager: yield a session and ``close`` it on exit."""
        session = cls.get_session()
        try:
            yield session
        finally:
            await session.close()

    @classmethod
    async def close(cls) -> None:
        """Dispose of the pool and clear the shared engine and session maker."""
        if cls._engine is not None:
            await cls._engine.dispose()
        cls._engine = None
        cls._session_maker = None
