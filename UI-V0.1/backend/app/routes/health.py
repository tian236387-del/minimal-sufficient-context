from __future__ import annotations

from fastapi import APIRouter, Request

from ..providers import ProviderError
from .dependencies import get_database, get_provider


router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health(request: Request):
    database = get_database(request)
    settings = request.app.state.settings
    with database.connection() as connection:
        connection.execute("SELECT 1").fetchone()
    return {
        "ok": True,
        "database": settings.database_path.name,
        "db_path": str(settings.database_path),
        "schema_version": database.current_version(),
        "ollama_base_url": settings.ollama_base_url,
        "default_model": settings.default_model,
    }


@router.get("/models")
async def models(request: Request):
    provider = get_provider(request)
    settings = request.app.state.settings
    try:
        available_models = await provider.list_models()
    except ProviderError:
        return {"models": [settings.default_model], "provider_available": False}
    return {
        "models": available_models or [settings.default_model],
        "provider_available": True,
    }
