from fastapi import FastAPI

app = FastAPI(title="Güd Vector Agent Suite")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
