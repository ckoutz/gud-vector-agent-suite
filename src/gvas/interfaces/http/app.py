from fastapi import FastAPI

from gvas.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Güd Vector Agent Suite")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
