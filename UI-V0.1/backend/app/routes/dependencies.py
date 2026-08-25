from __future__ import annotations

from fastapi import HTTPException, Request

from ..database import Database
from ..providers import (
    ChatProvider,
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_provider(request: Request) -> ChatProvider:
    return request.app.state.provider


def provider_http_exception(error: ProviderError) -> HTTPException:
    if isinstance(error, ProviderUnavailableError):
        return HTTPException(status_code=503, detail=str(error))
    if isinstance(error, ProviderTimeoutError):
        return HTTPException(status_code=504, detail=str(error))
    if isinstance(error, ProviderResponseError):
        return HTTPException(status_code=502, detail=str(error))
    return HTTPException(status_code=502, detail="Model provider failed")

