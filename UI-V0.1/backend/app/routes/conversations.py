from __future__ import annotations

import json
import re

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ..context_compiler import ancestor_path, compile_context, context_diff
from ..repository import (
    branch_path_ids,
    create_branch,
    create_conversation,
    delete_branch_tree,
    delete_conversation,
    expected_branch_parent,
    get_branch,
    get_conversation,
    get_message,
    list_branches,
    list_conversations,
    list_messages,
    rename_branch,
    row_to_branch,
    set_active_state,
    update_conversation_settings,
)
from ..schemas import (
    BranchCreate,
    BranchUpdate,
    ConversationCreate,
    ConversationUpdate,
)
from .dependencies import get_database


router = APIRouter(prefix="/api", tags=["conversations"])


def _conversation_payload(connection, conversation_id: int) -> dict:
    return {
        "conversation": dict(get_conversation(connection, conversation_id)),
        "messages": list_messages(connection, conversation_id),
        "branches": list_branches(connection, conversation_id),
    }


@router.get("/conversations")
def conversation_list(request: Request):
    database = get_database(request)
    with database.connection() as connection:
        return {"conversations": list_conversations(connection)}


@router.post("/conversations")
def conversation_create(body: ConversationCreate, request: Request):
    database = get_database(request)
    settings = request.app.state.settings
    with database.transaction() as connection:
        row = create_conversation(
            connection,
            body.title.strip(),
            body.system_prompt or settings.default_system_prompt,
            body.model or settings.default_model,
            body.token_budget,
        )
        return dict(row)


@router.get("/conversations/{conversation_id}")
def conversation_get(conversation_id: int, request: Request):
    database = get_database(request)
    with database.connection() as connection:
        return _conversation_payload(connection, conversation_id)


@router.patch("/conversations/{conversation_id}")
def conversation_update(
    conversation_id: int,
    body: ConversationUpdate,
    request: Request,
):
    database = get_database(request)
    with database.transaction() as connection:
        if body.title is not None or body.token_budget is not None:
            update_conversation_settings(
                connection,
                conversation_id,
                title=body.title.strip() if body.title is not None else None,
                token_budget=body.token_budget,
            )
        if "active_branch_id" in body.model_fields_set:
            branch = get_branch(connection, body.active_branch_id)
            if "active_message_id" in body.model_fields_set:
                active_message_id = body.active_message_id
            else:
                active_message_id = expected_branch_parent(branch)
            set_active_state(
                connection,
                conversation_id,
                body.active_branch_id,
                active_message_id,
            )
        return _conversation_payload(connection, conversation_id)


@router.post("/conversations/{conversation_id}/branches")
def branch_create(
    conversation_id: int,
    body: BranchCreate,
    request: Request,
):
    database = get_database(request)
    with database.transaction() as connection:
        conversation = get_conversation(connection, conversation_id)
        forked_from_message_id = body.forked_from_message_id
        if (
            "forked_from_message_id" not in body.model_fields_set
            and forked_from_message_id is None
        ):
            forked_from_message_id = conversation["active_message_id"]
        branch = create_branch(
            connection,
            conversation_id,
            forked_from_message_id,
            body.name,
        )
        return row_to_branch(branch, branch_path_ids(connection, branch))


@router.patch("/branches/{branch_id}")
def branch_update(branch_id: int, body: BranchUpdate, request: Request):
    database = get_database(request)
    with database.transaction() as connection:
        branch = rename_branch(connection, branch_id, body.name)
        return row_to_branch(branch, branch_path_ids(connection, branch))


@router.delete("/branches/{branch_id}")
def branch_delete(branch_id: int, request: Request):
    database = get_database(request)
    with database.transaction() as connection:
        result = delete_branch_tree(connection, branch_id)
    return {"ok": True, **result}


def _export_filename(branch: dict, suffix: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", branch["name"]).strip("-")
    return f"{safe_name or 'branch'}-{branch['id']}.{suffix}"


@router.get("/branches/{branch_id}/export")
def branch_export(
    branch_id: int,
    request: Request,
    format: str = Query(default="markdown", pattern="^(markdown|json)$"),
):
    database = get_database(request)
    with database.connection() as connection:
        branch_row = get_branch(connection, branch_id)
        branch = row_to_branch(
            branch_row,
            branch_path_ids(connection, branch_row),
        )
        conversation = dict(
            get_conversation(connection, branch_row["conversation_id"])
        )
        messages_by_id = {
            item["id"]: item
            for item in list_messages(connection, branch_row["conversation_id"])
        }
        messages = [
            messages_by_id[message_id]
            for message_id in branch["path_message_ids"]
            if message_id in messages_by_id
        ]

    if format == "json":
        content = json.dumps(
            {
                "export_version": "0.2",
                "conversation": conversation,
                "branch": branch,
                "messages": messages,
            },
            ensure_ascii=False,
            indent=2,
        )
        filename = _export_filename(branch, "json")
        media_type = "application/json"
    else:
        lines = [
            f"# {conversation['title']}",
            "",
            f"- Branch: {branch['name']}",
            f"- Model: {conversation['model']}",
            f"- Token budget: {conversation['token_budget']}",
            "",
            "## System Prompt",
            "",
            conversation["system_prompt"],
            "",
            "## Messages",
        ]
        for message in messages:
            lines.extend(
                [
                    "",
                    f"### {message['role'].title()} #{message['id']}",
                    "",
                    message["content"],
                ]
            )
        content = "\n".join(lines) + "\n"
        filename = _export_filename(branch, "md")
        media_type = "text/markdown"

    return Response(
        content=content,
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/messages/{message_id}/context")
def context_inspect(message_id: int, request: Request):
    database = get_database(request)
    with database.connection() as connection:
        message = get_message(connection, message_id)
        conversation = get_conversation(connection, message["conversation_id"])
        summary_branch_id = None
        if conversation["active_branch_id"] is not None:
            active_branch = get_branch(connection, conversation["active_branch_id"])
            if message_id in branch_path_ids(connection, active_branch):
                summary_branch_id = active_branch["id"]
        full_path = ancestor_path(connection, message_id, conversation["id"])
        branch_context = compile_context(
            connection,
            conversation,
            message_id,
            None,
            strategy="branch",
            branch_id=summary_branch_id,
        )
        linear_context = compile_context(
            connection,
            conversation,
            message_id,
            None,
            strategy="linear",
        )
        full_path_ids = {item["id"] for item in full_path}
        all_messages = list_messages(connection, conversation["id"])
        excluded = [
            item for item in all_messages if item["id"] not in full_path_ids
        ]
        included_ids = set(branch_context.included_message_ids)
        truncated_ids = set(branch_context.truncated_message_ids)
        return {
            "conversation_id": conversation["id"],
            "model": conversation["model"],
            "system_prompt": conversation["system_prompt"],
            "selected_message_id": message_id,
            "active_branch_id": active_branch["id"] if active_branch is not None else None,
            "active_branch_name": active_branch["name"] if active_branch is not None else None,
            "context_branch_id": summary_branch_id,
            "context_explanation": {
                "strategy": "branch",
                "rule": "只沿当前消息的祖先路径编译上下文",
                "excluded_rule": "其他 sibling 分支不会进入 Branch 上下文",
            },
            "active_path": [
                item for item in full_path if item["id"] in included_ids
            ],
            "truncated_path": [
                item for item in full_path if item["id"] in truncated_ids
            ],
            "full_path_message_count": len(full_path),
            "excluded_siblings": excluded,
            "active_message_count": len(branch_context.included_message_ids),
            "excluded_message_count": len(excluded),
            "last_prompt_tokens": message["prompt_tokens"],
            "last_response_tokens": message["response_tokens"],
            "token_budget": conversation["token_budget"],
            "estimated_prompt_tokens": branch_context.estimated_tokens,
            "unbounded_estimated_tokens": branch_context.unbounded_estimated_tokens,
            "branch_context": branch_context.summary(),
            "linear_context": linear_context.summary(),
            "context_diff": context_diff(linear_context, branch_context),
        }


@router.delete("/conversations/{conversation_id}")
def conversation_delete(conversation_id: int, request: Request):
    database = get_database(request)
    with database.transaction() as connection:
        delete_conversation(connection, conversation_id)
    return {"ok": True}
