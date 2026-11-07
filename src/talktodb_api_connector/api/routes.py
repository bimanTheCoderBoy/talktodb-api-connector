"""HTTP routes for talktodb-api-connector."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from talktodb_api_connector.api.schemas import (
    RunDbQueryOutput,
    RunDbQueryRequest,
    SettingsPublic,
)
from talktodb_api_connector.service import QueryService
from talktodb_api_connector.unit_of_work import UnitOfWork

router = APIRouter()


async def get_unit_of_work():
    """Request-scoped unit of work: session + repository; closes on exit."""
    async with UnitOfWork() as uow:
        yield uow


def get_query_service(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_unit_of_work)],
) -> QueryService:
    """Build :class:`QueryService` from app settings and the request-scoped unit of work."""
    return QueryService(uow, request.app.state.settings)


@router.get("/settings", response_model=SettingsPublic)
async def get_settings(request: Request) -> SettingsPublic:
    """Return loaded application settings without the database password."""
    return SettingsPublic.model_validate(
        request.app.state.settings.model_dump(exclude={"database": {"password": True}})
    )


@router.post("/query", response_model=RunDbQueryOutput)
async def post_query(
    body: RunDbQueryRequest,
    service: Annotated[QueryService, Depends(get_query_service)],
) -> RunDbQueryOutput:
    """Execute SQL; delegates to :class:`QueryService`."""
    return await service.run_query(body.sql, body.params)
