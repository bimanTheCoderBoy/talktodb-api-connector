"""Minimal data access: raw SQL through an :class:`AsyncSession`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SelectResult(BaseModel):
    """Row-returning SQL (``SELECT``, ``INSERT ... RETURNING``, etc.)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["select"] = Field(
        default="select",
        description='Tag value; always the string ``"select"``.',
    )
    columns: list[str] = Field(
        ...,
        description="Column names in order, from :meth:`sqlalchemy.engine.Result.keys`.",
    )
    rows: list[dict[str, Any]] = Field(
        ...,
        description="Result rows; each mapping column name to a cell value.",
    )


class AffectedResult(BaseModel):
    """Non-row-returning DML (``UPDATE`` / ``INSERT`` / ``DELETE`` without a row set)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["affected"] = Field(
        default="affected",
        description='Tag value; always the string ``"affected"``.',
    )
    rowcount: int = Field(
        ...,
        description=(
            "Rows affected from :attr:`sqlalchemy.engine.Result.rowcount`; "
            "drivers may use ``-1`` when unknown."
        ),
    )


class ErrorResult(BaseModel):
    """Execution failed before a successful structured result could be built."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["error"] = Field(
        default="error",
        description='Tag value; always the string ``"error"``.',
    )
    message: str = Field(..., description="Exception message or driver text.")


SqlExecuteResult = Annotated[
    Union[SelectResult, AffectedResult, ErrorResult],
    Field(discriminator="type"),
]


class SqlRepository:
    """Runs textual SQL using a composed async session.

    Use :meth:`execute_raw` to run SQL strings. It returns a **Pydantic v2** model
    (:class:`SelectResult`, :class:`AffectedResult`, or :class:`ErrorResult`), tagged
    by ``type``. See :class:`SqlExecuteResult` for the discriminated union.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def execute_raw(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> SqlExecuteResult:
        """Execute a raw SQL string with optional bound parameters.

        Returns one of three **Pydantic** result models (discriminated on ``type``):

        **1 — :class:`SelectResult`** — result set (when ``result.returns_rows`` is true),

        including ``SELECT`` and statements like ``INSERT ... RETURNING``::

            SelectResult(
                type="select",
                columns=["id", "name"],
                rows=[
                    {"id": 1, "name": "alice"},
                    {"id": 2, "name": "bob"},
                ],
            )

        **2 — :class:`AffectedResult`** — row count only (typical ``UPDATE`` / ``INSERT`` /
        ``DELETE`` without a returning clause). After a successful non-row-returning
        execution, :meth:`sqlalchemy.ext.asyncio.AsyncSession.flush` is awaited so
        pending ORM state (if any) is synchronized before returning; errors still skip
        flush and return :class:`ErrorResult`.

        **3 — :class:`ErrorResult`** — execution failed::

            ErrorResult(type="error", message="...")
        """
        stmt = text(sql)
        try:
            if params is None:
                result = await self._session.execute(stmt)
            else:
                result = await self._session.execute(stmt, params)
        except Exception as exc:
            return ErrorResult(message=str(exc))

        if result.returns_rows:
            columns = list(result.keys())
            rows = [dict(row._mapping) for row in result.fetchall()]
            return SelectResult(columns=columns, rows=rows)

        await self._session.flush()

        rowcount = result.rowcount
        if rowcount is None:
            rowcount = 0
        return AffectedResult(rowcount=rowcount)
