"""Service layer between API routes and the SQL repository."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from talktodb_api_connector.config import Settings
from talktodb_api_connector.repository import (
    AffectedResult,
    ErrorResult,
    SelectResult,
    SqlExecuteResult,
)
from talktodb_api_connector.api.schemas import RunDbQueryOutput
from talktodb_api_connector.unit_of_work import UnitOfWork


def _looks_like_read_only_sql(sql: str) -> bool:
    """True if stripped SQL appears to start with SELECT or WITH (common read-only)."""
    head = sql.lstrip()
    if not head:
        return False
    upper = head.upper()
    return upper.startswith("SELECT") or upper.startswith("WITH")


def _sql_result_to_run_db_output(result: SqlExecuteResult) -> RunDbQueryOutput:
    """Map repository union types to :class:`RunDbQueryOutput`."""
    if isinstance(result, SelectResult):
        column_names = result.columns
        rows_list = [
            [row.get(c) for c in column_names] for row in result.rows
        ]
        return RunDbQueryOutput(
            success=True,
            columns=column_names,
            rows=rows_list,
            row_count=len(result.rows),
        )
    if isinstance(result, AffectedResult):
        return RunDbQueryOutput(
            success=True,
            row_count=result.rowcount,
        )
    assert isinstance(result, ErrorResult)
    return RunDbQueryOutput(success=False, error=result.message)


def _log_query(sql: str, output: RunDbQueryOutput) -> None:
    """One line per attempt: SQL text, outcome, row_count on success (never log result rows)."""
    if output.success:
        logger.info(
            "query attempt sql=%r outcome=success row_count=%s",
            sql,
            output.row_count,
        )
    else:
        logger.info(
            "query attempt sql=%r outcome=failure message=%s",
            sql,
            output.error or "unknown error",
        )


class QueryService:
    """Policy and orchestration between routes and :class:`~talktodb_api_connector.unit_of_work.UnitOfWork`.

    Responsibilities:

    - **Business rules** — e.g. rejecting empty SQL, enforcing ``Settings.database.read_only``.
    - **Policy** — deciding *whether* a query may run before delegating to the repo.
    
    """

    def __init__(self, uow: UnitOfWork, settings: Settings) -> None:
        self._uow = uow
        self._settings = settings

    async def run_query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> RunDbQueryOutput:
        """Validate input, apply read-only policy, execute, commit on success, return output."""
        stripped = sql.strip()
        if not stripped:
            output = RunDbQueryOutput(success=False, error="SQL query is empty.")
            _log_query(stripped, output)
            return output

        if self._settings.database.read_only and not _looks_like_read_only_sql(stripped):
            output = RunDbQueryOutput(
                success=False,
                error=(
                    "read_only mode is enabled: only SELECT (or WITH ... SELECT) "
                    "queries are allowed."
                ),
            )
            _log_query(stripped, output)
            return output

        raw = await self._uow.repository.execute_raw(stripped, params)
        if isinstance(raw, ErrorResult):
            output = _sql_result_to_run_db_output(raw)
            _log_query(stripped, output)
            return output
        try:
            await self._uow.commit()
        except Exception as exc:
            await self._uow.rollback()
            output = RunDbQueryOutput(success=False, error=str(exc))
            _log_query(stripped, output)
            return output
        output = _sql_result_to_run_db_output(raw)
        _log_query(stripped, output)
        return output
