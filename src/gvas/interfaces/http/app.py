from fastapi import APIRouter, FastAPI

from gvas.config import Settings


def create_app(settings: Settings | None = None, routers: tuple[APIRouter, ...] = ()) -> FastAPI:
    app = FastAPI(title="Güd Vector Agent Suite")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    for router in routers:
        app.include_router(router)
    return app


app = create_app()
