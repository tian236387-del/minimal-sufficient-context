from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..repository import (
    branch_path_ids,
    get_branch,
    get_conversation,
    get_message,
    row_to_branch,
    row_to_message,
)
from ..schemas import MergeCreate, MergePreviewRequest, MergeRollback, SummaryCreate
from ..summary_service import (
    SummaryError,
    build_dag,
    create_summary,
    execute_merge,
    get_summary,
    list_merges,
    list_summaries,
    merge_payload,
    preview_merge,
    rollback_merge,
    summary_payload,
)
from .dependencies import get_database


router = APIRouter(prefix="/api", tags=["summaries", "merges", "dag"])


def _summary_arguments(body: SummaryCreate, branch_id: int | None) -> dict:
    if branch_id is not None and body.branch_id is not None and body.branch_id != branch_id:
        raise SummaryError("Summary branch_id does not match the route branch")
    return {
        "branch_id": branch_id if branch_id is not None else body.branch_id,
        "anchor_message_id": body.anchor_message_id,
        "title": body.title,
        "content": body.content,
        "source_message_ids": body.source_message_ids,
        "claims": [claim.model_dump(exclude_none=True) for claim in body.claims],
    }


@router.get("/messages/{message_id}")
def message_get(message_id: int, request: Request):
    database = get_database(request)
    with database.connection() as connection:
        return row_to_message(get_message(connection, message_id))


@router.get("/conversations/{conversation_id}/summaries")
def conversation_summaries(
    conversation_id: int,
    request: Request,
    branch_id: int | None = Query(default=None, gt=0),
    include_orphaned: bool = False,
):
    database = get_database(request)
    with database.connection() as connection:
        return {
            "conversation_id": conversation_id,
            "summaries": list_summaries(
                connection,
                conversation_id,
                branch_id,
                include_orphaned=include_orphaned,
            ),
        }


@router.post("/conversations/{conversation_id}/summaries")
def conversation_summary_create(
    conversation_id: int,
    body: SummaryCreate,
    request: Request,
):
    database = get_database(request)
    with database.transaction() as connection:
        conversation = get_conversation(connection, conversation_id)
        branch_id = body.branch_id or conversation["active_branch_id"]
        payload = _summary_arguments(body, branch_id)
        return create_summary(connection, conversation_id, **payload)


@router.get("/branches/{branch_id}/summaries")
def branch_summaries(
    branch_id: int,
    request: Request,
    include_orphaned: bool = False,
):
    database = get_database(request)
    with database.connection() as connection:
        branch = get_branch(connection, branch_id)
        return {
            "conversation_id": branch["conversation_id"],
            "branch": row_to_branch(branch, branch_path_ids(connection, branch)),
            "summaries": list_summaries(
                connection,
                branch["conversation_id"],
                branch_id,
                include_orphaned=include_orphaned,
            ),
        }


@router.post("/branches/{branch_id}/summaries")
def branch_summary_create(
    branch_id: int,
    body: SummaryCreate,
    request: Request,
):
    database = get_database(request)
    with database.transaction() as connection:
        branch = get_branch(connection, branch_id)
        payload = _summary_arguments(body, branch_id)
        return create_summary(connection, branch["conversation_id"], **payload)


@router.get("/summaries/{summary_id}")
def summary_get(summary_id: int, request: Request):
    database = get_database(request)
    with database.connection() as connection:
        return summary_payload(connection, get_summary(connection, summary_id))


@router.get("/conversations/{conversation_id}/merges")
def conversation_merges(conversation_id: int, request: Request):
    database = get_database(request)
    with database.connection() as connection:
        return {"conversation_id": conversation_id, "merges": list_merges(connection, conversation_id)}


@router.post("/conversations/{conversation_id}/merges/preview")
def merge_preview(
    conversation_id: int,
    body: MergePreviewRequest,
    request: Request,
):
    database = get_database(request)
    with database.connection() as connection:
        return preview_merge(
            connection,
            conversation_id,
            target_branch_id=body.target_branch_id,
            source_branch_id=body.source_branch_id,
            target_summary_ids=body.target_summary_ids,
            source_summary_ids=body.source_summary_ids,
        )


@router.post("/conversations/{conversation_id}/merges")
def merge_create(
    conversation_id: int,
    body: MergeCreate,
    request: Request,
):
    database = get_database(request)
    with database.transaction() as connection:
        return execute_merge(
            connection,
            conversation_id,
            target_branch_id=body.target_branch_id,
            source_branch_id=body.source_branch_id,
            target_summary_ids=body.target_summary_ids,
            source_summary_ids=body.source_summary_ids,
            preview_token=body.preview_token,
            resolutions=body.resolutions,
            name=body.name,
            activate=body.activate,
        )


@router.get("/merges/{merge_id}")
def merge_get(merge_id: int, request: Request):
    database = get_database(request)
    with database.connection() as connection:
        return merge_payload(connection, merge_id)


@router.post("/merges/{merge_id}/rollback")
def merge_rollback(
    merge_id: int,
    body: MergeRollback,
    request: Request,
):
    database = get_database(request)
    with database.transaction() as connection:
        return rollback_merge(connection, merge_id, reason=body.reason)


@router.get("/conversations/{conversation_id}/dag")
def conversation_dag(conversation_id: int, request: Request):
    database = get_database(request)
    with database.connection() as connection:
        return build_dag(connection, conversation_id)
