"""The customer's visit link: GET shows the visit (so a mail client's
prefetch never acts), POST cancels or asks for another time."""

from fastapi import APIRouter, Depends, params
from fastapi.responses import HTMLResponse

from gvas.infrastructure.visit_links import VisitLinkService, visit_page


def create_visit_router(
    service: VisitLinkService, dependencies: list[params.Depends] | None = None
) -> APIRouter:
    router = APIRouter()
    limited = dependencies or []

    @router.get("/v1/visits/{token}", dependencies=limited)
    async def view_visit(token: str) -> HTMLResponse:
        return HTMLResponse(visit_page(await service.preview(token)))

    @router.post("/v1/visits/{token}", dependencies=limited)
    async def act_on_visit(token: str, action: str = "") -> HTMLResponse:
        return HTMLResponse(visit_page(await service.act(token, action)))

    return router


__all__ = ["Depends", "create_visit_router"]
