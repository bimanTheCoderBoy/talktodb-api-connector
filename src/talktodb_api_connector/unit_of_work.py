"""Unit of work: one async session and repository per request scope."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from talktodb_api_connector.database_registry import DatabaseConnectionRegistry
from talktodb_api_connector.repository import SqlRepository


class UnitOfWork:
    """Bundles an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and :class:`SqlRepository`.

    The session is created in :meth:`__aenter__`; :attr:`repository` is built lazily on
    first access and cached on ``self._repository``.

    Use as ``async with UnitOfWork() as uow:`` — on exit, if the session is still open,
    it is closed so uncommitted work is discarded and the connection returns to the pool
    (unless you already called :meth:`commit` or :meth:`rollback`). Call :meth:`commit`
    after successful work to persist. :meth:`rollback` closes the session without committing,
    ending the transaction per application policy.
    """

    def __init__(self) -> None:
        self._session: AsyncSession | None = None
        self._repository: SqlRepository | None = None

    @property
    def repository(self) -> SqlRepository:
        """Lazily create and cache :class:`SqlRepository` for the active session."""
        if self._session is None:
            raise RuntimeError(
                "UnitOfWork has no active session; use `async with UnitOfWork()` first."
            )
        if self._repository is None:
            self._repository = SqlRepository(self._session)
        return self._repository

    async def __aenter__(self) -> UnitOfWork:
        self._session = DatabaseConnectionRegistry.get_session()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._repository = None

    async def commit(self) -> None:
        """Persist the current transaction."""
        if self._session is None:
            raise RuntimeError("UnitOfWork session is not active (already closed or not entered).")
        await self._session.commit()

    async def rollback(self) -> None:
        """End the transaction without persisting by closing the session."""
        if self._session is None:
            return
        await self._session.close()
        self._session = None
        self._repository = None
