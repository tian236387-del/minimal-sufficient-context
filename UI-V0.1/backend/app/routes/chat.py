from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterator
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..context_compiler import CompiledContext, compile_context, context_diff
from ..providers import ProviderError
from ..repository import (
    BranchOperationError,
    InvalidParentError,
    RecordNotFoundError,
    advance_branch,
    branch_path_ids,
    create_branch,
    expected_branch_parent,
    find_branch_for_parent,
    get_branch,
    get_conversation,
    insert_message,
    row_to_branch,
    row_to_message,
    update_conversation_model,
    validate_branch_parent,
    validate_parent,
)
from ..schemas import ChatRequest, ContextCompareRequest
from .dependencies import get_database, get_provider, provider_http_exception


router = APIRouter(prefix="/api", tags=["chat"])


def _prepare_chat(connection: sqlite3.Connection, body: ChatRequest):
    conversation = get_conversation(connection, body.conversation_id)
    parent_id = body.parent_id
    branch = None

    if body.branch_id is not None:
        branch = get_branch(connection, body.branch_id)
        if branch["conversation_id"] != body.conversation_id:
            raise BranchOperationError("Branch belongs to another conversation")
        if parent_id is None:
            parent_id = expected_branch_parent(branch)
        validate_branch_parent(connection, branch, parent_id)
    else:
        validate_parent(connection, body.conversation_id, parent_id)
        branch = find_branch_for_parent(connection, conversation, parent_id)

    compiled = compile_context(
        connection,
        conversation,
        parent_id,
        body.content,
        strategy="branch",
        branch_id=branch["id"] if branch is not None else None,
    )
    model = body.model or conversation["model"]
    return conversation, branch, parent_id, compiled, model


def _persist_exchange(
    database,
    *,
    conversation_id: int,
    resolved_branch_id: Optional[int],
    parent_id: Optional[int],
    user_content: str,
    assistant_content: str,
    model: str,
    original_model: str,
    prompt_tokens: Optional[int],
    response_tokens: Optional[int],
) -> dict:
    with database.transaction() as connection:
        get_conversation(connection, conversation_id)
        if resolved_branch_id is None:
            validate_parent(connection, conversation_id, parent_id)
            branch = create_branch(connection, conversation_id, parent_id)
        else:
            branch = get_branch(connection, resolved_branch_id)
            if branch["conversation_id"] != conversation_id:
                raise BranchOperationError("Branch belongs to another conversation")
            validate_branch_parent(connection, branch, parent_id)

        user_row = insert_message(
            connection,
            conversation_id,
            parent_id,
            "user",
            user_content,
        )
        assistant_row = insert_message(
            connection,
            conversation_id,
            user_row["id"],
            "assistant",
            assistant_content,
            prompt_tokens,
            response_tokens,
        )
        branch = advance_branch(
            connection,
            branch["id"],
            user_row["id"],
            assistant_row["id"],
        )
        if model != original_model:
            update_conversation_model(connection, conversation_id, model)
        branch_payload = row_to_branch(
            branch,
            branch_path_ids(connection, branch),
        )

    return {
        "user_message": row_to_message(user_row),
        "assistant_message": row_to_message(assistant_row),
        "branch": branch_payload,
        "model": model,
    }


@router.post("/chat")
async def chat(body: ChatRequest, request: Request):
    database = get_database(request)
    provider = get_provider(request)

    with database.connection() as connection:
        conversation, branch, parent_id, compiled, model = _prepare_chat(
            connection, body
        )

    try:
        result = await provider.chat(model, compiled.messages)
    except ProviderError as error:
        raise provider_http_exception(error) from error

    payload = _persist_exchange(
        database,
        conversation_id=body.conversation_id,
        resolved_branch_id=branch["id"] if branch is not None else None,
        parent_id=parent_id,
        user_content=body.content,
        assistant_content=result.content,
        model=model,
        original_model=conversation["model"],
        prompt_tokens=result.prompt_tokens,
        response_tokens=result.response_tokens,
    )
    payload["compiled_context_message_ids"] = compiled.included_message_ids
    payload["context"] = compiled.summary()
    return payload


def _sse(event: str, payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n"


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request):
    database = get_database(request)
    provider = get_provider(request)

    with database.connection() as connection:
        conversation, branch, parent_id, compiled, model = _prepare_chat(
            connection, body
        )

    resolved_branch_id = branch["id"] if branch is not None else None
    original_model = conversation["model"]

    async def events():
        chunks = []
        prompt_tokens = None
        response_tokens = None
        try:
            yield _sse(
                "context",
                {"model": model, "context": compiled.summary()},
            )
            async for stream_event in provider.stream_chat(model, compiled.messages):
                if stream_event.content:
                    chunks.append(stream_event.content)
                    yield _sse("delta", {"content": stream_event.content})
                if stream_event.done:
                    prompt_tokens = stream_event.prompt_tokens
                    response_tokens = stream_event.response_tokens

            assistant_content = "".join(chunks).strip()
            if not assistant_content:
                raise ValueError("Model provider returned an empty assistant message")
            payload = _persist_exchange(
                database,
                conversation_id=body.conversation_id,
                resolved_branch_id=resolved_branch_id,
                parent_id=parent_id,
                user_content=body.content,
                assistant_content=assistant_content,
                model=model,
                original_model=original_model,
                prompt_tokens=prompt_tokens,
                response_tokens=response_tokens,
            )
            payload["compiled_context_message_ids"] = compiled.included_message_ids
            payload["context"] = compiled.summary()
            yield _sse("done", payload)
        except ProviderError as error:
            http_error = provider_http_exception(error)
            yield _sse(
                "error",
                {"detail": str(error), "status_code": http_error.status_code},
            )
        except (
            BranchOperationError,
            InvalidParentError,
            RecordNotFoundError,
            sqlite3.IntegrityError,
            ValueError,
        ) as error:
            yield _sse("error", {"detail": str(error), "status_code": 409})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _validate_compare_branch(
    connection: sqlite3.Connection,
    body: ContextCompareRequest,
) -> Optional[int]:
    parent_id = body.parent_id
    if body.branch_id is None:
        validate_parent(connection, body.conversation_id, parent_id)
        return parent_id

    branch = get_branch(connection, body.branch_id)
    if branch["conversation_id"] != body.conversation_id:
        raise BranchOperationError("Branch belongs to another conversation")
    if parent_id is None:
        parent_id = expected_branch_parent(branch)
    if parent_id is not None and parent_id not in branch_path_ids(connection, branch):
        raise BranchOperationError("Message is not visible on the selected branch")
    return parent_id


def _comparison_result(result, compiled: CompiledContext) -> dict:
    return {
        "answer": result.content,
        "prompt_tokens": result.prompt_tokens,
        "response_tokens": result.response_tokens,
        "context": compiled.summary(),
    }


@router.post("/context/compare")
async def compare_contexts(body: ContextCompareRequest, request: Request):
    database = get_database(request)
    provider = get_provider(request)

    with database.connection() as connection:
        conversation = get_conversation(connection, body.conversation_id)
        parent_id = _validate_compare_branch(connection, body)
        branch_context = compile_context(
            connection,
            conversation,
            parent_id,
            body.content,
            strategy="branch",
            token_budget=body.token_budget,
            branch_id=body.branch_id,
        )
        linear_context = compile_context(
            connection,
            conversation,
            parent_id,
            body.content,
            strategy="linear",
            token_budget=body.token_budget,
        )
        model = body.model or conversation["model"]

    try:
        linear_result, branch_result = await asyncio.gather(
            provider.chat(model, linear_context.messages),
            provider.chat(model, branch_context.messages),
        )
    except ProviderError as error:
        raise provider_http_exception(error) from error

    return {
        "model": model,
        "question": body.content,
        "linear": _comparison_result(linear_result, linear_context),
        "branch": _comparison_result(branch_result, branch_context),
        "context_diff": context_diff(linear_context, branch_context),
        "persisted": False,
    }
