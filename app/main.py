from fastapi import FastAPI

app = FastAPI(title="Transfer Core", docs_url="/docs", openapi_url="/openapi.json")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
