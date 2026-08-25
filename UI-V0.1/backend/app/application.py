from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .context_compiler import ContextBudgetError, ContextIntegrityError
from .database import Database
from .providers import ChatProvider, OllamaProvider
from .repository import (
    BranchOperationError,
    InvalidParentError,
    ProtectedBranchError,
    RecordNotFoundError,
    bootstrap_branches,
)
from .routes import chat_router, conversations_router, health_router, knowledge_router
from .summary_service import (
    MergeConflictError,
    MergePreconditionError,
    MergePreviewStaleError,
    SummaryError,
)


def create_app(
    settings: Settings | None = None,
    provider: ChatProvider | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(
        settings.database_path,
        settings.migrations_path,
        settings.backup_path,
        settings.backup_before_migrate,
    )
    provider = provider or OllamaProvider(
        settings.ollama_base_url,
        settings.provider_request_timeout_seconds,
        settings.provider_models_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.migrate()
        with database.transaction() as connection:
            bootstrap_branches(connection)
        yield

    app = FastAPI(
        title="Minimal Sufficient Context API",
        version="0.2.1",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.provider = provider

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RecordNotFoundError)
    async def record_not_found(_: Request, error: RecordNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(InvalidParentError)
    async def invalid_parent(_: Request, error: InvalidParentError):
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.exception_handler(ProtectedBranchError)
    async def protected_branch(_: Request, error: ProtectedBranchError):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(BranchOperationError)
    async def invalid_branch(_: Request, error: BranchOperationError):
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.exception_handler(ContextBudgetError)
    async def context_budget(_: Request, error: ContextBudgetError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": str(error),
                "token_budget": error.token_budget,
                "required_tokens": error.required_tokens,
            },
        )

    @app.exception_handler(ContextIntegrityError)
    async def invalid_context(_: Request, error: ContextIntegrityError):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(MergeConflictError)
    async def merge_conflict(_: Request, error: MergeConflictError):
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(error),
                "conflicts": error.conflicts,
                "preview": error.preview,
            },
        )

    @app.exception_handler(MergePreconditionError)
    async def merge_precondition(_: Request, error: MergePreconditionError):
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(error),
                "blockers": error.blockers,
                "preview": error.preview,
            },
        )

    @app.exception_handler(MergePreviewStaleError)
    async def stale_merge_preview(_: Request, error: MergePreviewStaleError):
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(error),
                "expected_preview_token": error.expected,
                "received_preview_token": error.received,
            },
        )

    @app.exception_handler(SummaryError)
    async def summary_error(_: Request, error: SummaryError):
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.exception_handler(sqlite3.IntegrityError)
    async def sqlite_integrity_error(_: Request, __: sqlite3.IntegrityError):
        return JSONResponse(
            status_code=409,
            content={"detail": "Database integrity constraint failed"},
        )

    app.include_router(health_router)
    app.include_router(conversations_router)
    app.include_router(chat_router)
    app.include_router(knowledge_router)
    return app
