from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .repository import (
    BranchOperationError,
    RecordNotFoundError,
    ancestor_ids,
    branch_path_ids,
    create_merge_branch,
    expected_branch_parent,
    get_branch,
    get_conversation,
    get_message,
    row_to_branch,
    row_to_message,
    set_active_state,
)


class SummaryError(ValueError):
    pass


class SummarySourceError(SummaryError):
    pass


class MergePreviewStaleError(SummaryError):
    def __init__(self, expected: str, received: str) -> None:
        self.expected = expected
        self.received = received
        super().__init__(
            "Merge preview is stale; refresh the preview before executing the merge"
        )


class MergePreviewRequiredError(SummaryError):
    def __init__(self) -> None:
        super().__init__(
            "Run a merge preview and submit its preview_token before executing a merge"
        )


class MergeConflictError(SummaryError):
    def __init__(self, conflicts: list[dict], preview: dict) -> None:
        self.conflicts = conflicts
        self.preview = preview
        super().__init__(
            f"Merge has {len(conflicts)} unresolved conflict(s); "
            "choose an explicit resolution before merging"
        )


class MergePreconditionError(SummaryError):
    def __init__(self, blockers: list[dict], preview: dict) -> None:
        self.blockers = blockers
        self.preview = preview
        super().__init__(
            "Merge requires a current citable summary from both branches"
        )


CLAIM_PATTERN = re.compile(
    r"^\s*(?:[-*]\s*)?(?P<key>[^:=\uFF1A\n]{1,80}?)"
    r"\s*(?::|=|\uFF1A)\s+(?P<value>.{1,400})\s*$"
)
SKIPPED_CLAIM_KEYS = {
    "source",
    "sources",
    "citation",
    "citations",
    "target branch",
    "source branch",
    "merge snapshot",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def citation_for_message(message_id: int) -> str:
    return f"[m:{message_id}]"


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _short_text(value: str, limit: int = 320) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def _unique_ints(values: Iterable[int]) -> list[int]:
    result = []
    seen = set()
    for value in values:
        integer = int(value)
        if integer not in seen:
            seen.add(integer)
            result.append(integer)
    return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def extract_message_claims(message: dict) -> list[dict]:
    claims = []
    for line in str(message.get("content", "")).splitlines():
        match = CLAIM_PATTERN.match(line)
        if match is None:
            continue
        key = " ".join(match.group("key").split()).strip(" -*")
        value = " ".join(match.group("value").split())
        if not key or not value or _normalize(key) in SKIPPED_CLAIM_KEYS:
            continue
        claims.append(
            {
                "key": key,
                "value": value,
                "claim_text": f"{key}: {value}",
                "source_message_id": message["id"],
            }
        )
    return claims


def _summary_sources(
    connection: sqlite3.Connection,
    summary_id: int,
) -> list[dict]:
    rows = connection.execute(
        """
        SELECT ss.source_order, ss.source_branch_id, m.*
        FROM summary_sources ss
        JOIN messages m ON m.id = ss.message_id
        WHERE ss.summary_id = ?
        ORDER BY ss.source_order ASC, m.id ASC
        """,
        (summary_id,),
    ).fetchall()
    return [
        {
            "message_id": row["id"],
            "citation": citation_for_message(row["id"]),
            "source_order": row["source_order"],
            "branch_id": row["source_branch_id"],
            "message": row_to_message(row),
        }
        for row in rows
    ]


def _summary_claims(
    connection: sqlite3.Connection,
    summary_id: int,
) -> list[dict]:
    rows = connection.execute(
        """
        SELECT * FROM summary_claims
        WHERE summary_id = ?
        ORDER BY claim_order ASC, id ASC
        """,
        (summary_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "key": row["claim_key"],
            "value": row["claim_value"],
            "claim_text": row["claim_text"],
            "source_message_id": row["source_message_id"],
            "citation": citation_for_message(row["source_message_id"]),
        }
        for row in rows
    ]


def summary_payload(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    include_claims: bool = True,
) -> dict:
    sources = _summary_sources(connection, row["id"])
    payload = dict(row)
    payload.update(
        {
            "source_message_ids": [item["message_id"] for item in sources],
            "citation_count": len(sources),
            "sources": sources,
            "is_citable": row["status"] == "active" and bool(sources),
        }
    )
    if include_claims:
        payload["claims"] = _summary_claims(connection, row["id"])
    return payload


def get_summary(connection: sqlite3.Connection, summary_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM summaries WHERE id = ?", (summary_id,)
    ).fetchone()
    if row is None:
        raise RecordNotFoundError("Summary", summary_id)
    return row


def list_summaries(
    connection: sqlite3.Connection,
    conversation_id: int,
    branch_id: Optional[int] = None,
    *,
    include_orphaned: bool = False,
) -> list[dict]:
    get_conversation(connection, conversation_id)
    clauses = ["conversation_id = ?"]
    params: list[Any] = [conversation_id]
    if branch_id is not None:
        clauses.append("branch_id = ?")
        params.append(branch_id)
    if not include_orphaned:
        clauses.append("status != 'orphaned'")
    rows = connection.execute(
        f"""
        SELECT * FROM summaries
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC, id DESC
        """,
        tuple(params),
    ).fetchall()
    return [summary_payload(connection, row) for row in rows]


def _path_messages(
    connection: sqlite3.Connection,
    branch: sqlite3.Row,
    anchor_message_id: Optional[int] = None,
) -> list[dict]:
    leaf_id = anchor_message_id or expected_branch_parent(branch)
    if leaf_id is None:
        return []
    path_ids = ancestor_ids(connection, leaf_id)
    messages = {
        row["id"]: row_to_message(row)
        for row in connection.execute(
            "SELECT * FROM messages WHERE conversation_id = ?",
            (branch["conversation_id"],),
        ).fetchall()
    }
    return [messages[message_id] for message_id in path_ids if message_id in messages]


def _next_summary_version(
    connection: sqlite3.Connection,
    conversation_id: int,
    branch_id: Optional[int],
) -> int:
    if branch_id is None:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM summaries "
            "WHERE conversation_id = ? AND branch_id IS NULL",
            (conversation_id,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM summaries "
            "WHERE conversation_id = ? AND branch_id = ?",
            (conversation_id, branch_id),
        ).fetchone()
    return int(row["version"]) + 1


def _validate_summary_sources(
    connection: sqlite3.Connection,
    conversation_id: int,
    branch: Optional[sqlite3.Row],
    anchor_message_id: Optional[int],
    source_message_ids: Optional[list[int]],
    *,
    kind: str,
) -> tuple[Optional[int], list[int], dict[int, dict]]:
    if branch is not None and branch["conversation_id"] != conversation_id:
        raise SummarySourceError("Summary branch belongs to another conversation")
    if branch is not None and anchor_message_id is None:
        anchor_message_id = expected_branch_parent(branch)
    if anchor_message_id is not None:
        anchor = get_message(connection, anchor_message_id)
        if anchor["conversation_id"] != conversation_id:
            raise SummarySourceError("Summary anchor belongs to another conversation")
        if branch is not None and anchor_message_id not in branch_path_ids(connection, branch):
            raise SummarySourceError(
                "Summary anchor is not visible on the selected branch"
            )

    path_messages = _path_messages(connection, branch, anchor_message_id) if branch else []
    path_ids = {item["id"] for item in path_messages}

    if source_message_ids:
        selected_ids = _unique_ints(source_message_ids)
    elif path_messages:
        selected_ids = [item["id"] for item in path_messages]
    else:
        selected_ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM messages WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
        ]

    if not selected_ids:
        raise SummarySourceError("A summary must cite at least one original message")

    rows = connection.execute(
        """
        SELECT * FROM messages
        WHERE conversation_id = ?
          AND id IN ({})
        """.format(",".join("?" for _ in selected_ids)),
        (conversation_id, *selected_ids),
    ).fetchall()
    messages = {row["id"]: row_to_message(row) for row in rows}
    missing = [message_id for message_id in selected_ids if message_id not in messages]
    if missing:
        raise SummarySourceError(
            f"Summary source messages not found in conversation: {missing}"
        )
    if branch is not None and kind != "merge_snapshot":
        outside_path = [message_id for message_id in selected_ids if message_id not in path_ids]
        if outside_path:
            raise SummarySourceError(
                f"Summary sources must be visible on the selected branch: {outside_path}"
            )
    return anchor_message_id, selected_ids, messages


def _generated_summary_content(messages: list[dict]) -> str:
    lines = ["Extractive branch summary"]
    for message in messages:
        role = str(message["role"]).title()
        lines.append(
            f"- {role}: {_short_text(message['content'])} "
            f"{citation_for_message(message['id'])}"
        )
    lines.append("")
    lines.append(
        "Sources: "
        + " ".join(citation_for_message(message["id"]) for message in messages)
    )
    return "\n".join(lines)


def _ensure_citations(content: str, source_ids: list[int]) -> str:
    source_line = "Sources: " + " ".join(citation_for_message(message_id) for message_id in source_ids)
    if source_line in content:
        return content
    return f"{content.rstrip()}\n\n{source_line}"


def create_summary(
    connection: sqlite3.Connection,
    conversation_id: int,
    *,
    branch_id: Optional[int] = None,
    anchor_message_id: Optional[int] = None,
    title: Optional[str] = None,
    content: Optional[str] = None,
    source_message_ids: Optional[list[int]] = None,
    claims: Optional[list[dict]] = None,
    kind: str = "branch",
    source_branch_by_message: Optional[dict[int, int]] = None,
) -> dict:
    if kind not in {"branch", "merge_snapshot"}:
        raise SummaryError(f"Unsupported summary kind: {kind}")
    conversation = get_conversation(connection, conversation_id)
    branch = get_branch(connection, branch_id) if branch_id is not None else None
    anchor_message_id, selected_ids, messages = _validate_summary_sources(
        connection,
        conversation_id,
        branch,
        anchor_message_id,
        source_message_ids,
        kind=kind,
    )
    ordered_messages = [messages[message_id] for message_id in selected_ids]
    summary_content = (content or "").strip()
    if not summary_content:
        summary_content = _generated_summary_content(ordered_messages)
    summary_content = _ensure_citations(summary_content, selected_ids)
    summary_title = (title or "").strip() or (
        "Merge snapshot" if kind == "merge_snapshot" else "Branch summary"
    )
    version = _next_summary_version(connection, conversation_id, branch_id)
    timestamp = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO summaries (
            conversation_id, branch_id, anchor_message_id, title, content,
            kind, version, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (
            conversation["id"],
            branch_id,
            anchor_message_id,
            summary_title,
            summary_content,
            kind,
            version,
            timestamp,
            timestamp,
        ),
    )
    summary_id = int(cursor.lastrowid)
    for source_order, message_id in enumerate(selected_ids):
        connection.execute(
            """
            INSERT INTO summary_sources (
                summary_id, message_id, source_branch_id, source_order
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                summary_id,
                message_id,
                (source_branch_by_message or {}).get(message_id, branch_id),
                source_order,
            ),
        )

    claim_rows = []
    if claims:
        for claim_order, claim in enumerate(claims):
            key = str(claim.get("key", "")).strip()
            value = str(claim.get("value", "")).strip()
            source_message_id = claim.get("source_message_id")
            if not key or not value:
                raise SummaryError("Summary claims require both key and value")
            if source_message_id is None:
                source_message_id = selected_ids[0]
            source_message_id = int(source_message_id)
            if source_message_id not in selected_ids:
                raise SummarySourceError(
                    f"Claim source {source_message_id} is not cited by the summary"
                )
            claim_text = str(claim.get("claim_text") or f"{key}: {value}").strip()
            claim_rows.append((key, value, claim_text, source_message_id, claim_order))
    else:
        for message in ordered_messages:
            for claim_order, claim in enumerate(extract_message_claims(message)):
                claim_rows.append(
                    (
                        claim["key"],
                        claim["value"],
                        claim["claim_text"],
                        claim["source_message_id"],
                        claim_order,
                    )
                )

    if claims and claim_rows:
        claim_citation_block = "Claim citations:\n" + "\n".join(
            f"- {claim_text} {citation_for_message(source_message_id)}"
            for _, _, claim_text, source_message_id, _ in claim_rows
        )
        summary_content = f"{summary_content.rstrip()}\n\n{claim_citation_block}"
        connection.execute(
            "UPDATE summaries SET content = ?, updated_at = ? WHERE id = ?",
            (summary_content, utc_now(), summary_id),
        )

    for key, value, claim_text, source_message_id, claim_order in claim_rows:
        connection.execute(
            """
            INSERT INTO summary_claims (
                summary_id, claim_key, claim_value, claim_text,
                source_message_id, claim_order, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary_id,
                key,
                value,
                claim_text,
                source_message_id,
                claim_order,
                timestamp,
            ),
        )
    return summary_payload(connection, get_summary(connection, summary_id))


def _summary_context_rows(
    connection: sqlite3.Connection,
    branch_id: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT summary.*
        FROM summaries summary
        JOIN merge_operations merge ON merge.snapshot_summary_id = summary.id
        WHERE merge.result_branch_id = ?
          AND merge.status = 'completed'
          AND summary.status = 'active'
        ORDER BY merge.version ASC, summary.id ASC
        """,
        (branch_id,),
    ).fetchall()


def branch_summary_context(
    connection: sqlite3.Connection,
    branch_id: Optional[int],
) -> list[dict]:
    if branch_id is None:
        return []
    return [
        summary_payload(connection, row, include_claims=False)
        for row in _summary_context_rows(connection, branch_id)
    ]


def _validate_merge_branches(
    connection: sqlite3.Connection,
    conversation_id: int,
    target_branch_id: int,
    source_branch_id: int,
) -> tuple[sqlite3.Row, sqlite3.Row, list[int], list[int], Optional[int]]:
    if target_branch_id == source_branch_id:
        raise BranchOperationError("A branch cannot be merged with itself")
    get_conversation(connection, conversation_id)
    target = get_branch(connection, target_branch_id)
    source = get_branch(connection, source_branch_id)
    if target["conversation_id"] != conversation_id or source["conversation_id"] != conversation_id:
        raise BranchOperationError("Merge branches must belong to the conversation")
    target_path = branch_path_ids(connection, target)
    source_path = branch_path_ids(connection, source)
    common_base = None
    for target_id, source_id in zip(target_path, source_path):
        if target_id != source_id:
            break
        common_base = target_id
    return target, source, target_path, source_path, common_base


def _validate_merge_summaries(
    connection: sqlite3.Connection,
    conversation_id: int,
    branch: sqlite3.Row,
    summary_ids: Iterable[int],
) -> list[dict]:
    path_ids = set(branch_path_ids(connection, branch))
    head_message_id = expected_branch_parent(branch)
    result = []
    for summary_id in _unique_ints(summary_ids):
        summary = get_summary(connection, summary_id)
        if summary["conversation_id"] != conversation_id:
            raise SummarySourceError("Merge summary belongs to another conversation")
        if summary["status"] != "active":
            raise SummarySourceError(f"Summary {summary_id} is not citable")
        if summary["branch_id"] != branch["id"]:
            raise SummarySourceError(
                f"Summary {summary_id} belongs to another branch"
            )
        source_ids = {
            item["message_id"] for item in _summary_sources(connection, summary_id)
        }
        if summary["anchor_message_id"] != head_message_id or not path_ids.issubset(source_ids):
            raise SummarySourceError(
                f"Summary {summary_id} does not cover the current branch head"
            )
        result.append(summary_payload(connection, summary))
    return result


def _claim_groups(
    connection: sqlite3.Connection,
    branch: sqlite3.Row,
    selected_summaries: list[dict],
) -> dict[str, dict[str, dict]]:
    groups: dict[str, dict[str, dict]] = defaultdict(dict)

    def add_claim(claim: dict) -> None:
        key = str(claim["key"]).strip()
        value = str(claim["value"]).strip()
        normalized_key = _normalize(key)
        normalized_value = _normalize(value)
        if not normalized_key or not normalized_value:
            return
        bucket = groups[normalized_key]
        existing = bucket.get(normalized_value)
        source_id = int(claim["source_message_id"])
        if existing is None:
            bucket[normalized_value] = {
                "key": key,
                "value": value,
                "claim_text": claim.get("claim_text") or f"{key}: {value}",
                "source_message_ids": [source_id],
                "citations": [citation_for_message(source_id)],
            }
        elif source_id not in existing["source_message_ids"]:
            existing["source_message_ids"].append(source_id)
            existing["citations"].append(citation_for_message(source_id))

    for message in _path_messages(connection, branch):
        for claim in extract_message_claims(message):
            add_claim(claim)
    for summary in selected_summaries:
        for claim in summary.get("claims", []):
            add_claim(claim)
    return groups


def _conflicts_for_branches(
    connection: sqlite3.Connection,
    target: sqlite3.Row,
    source: sqlite3.Row,
    target_summaries: list[dict],
    source_summaries: list[dict],
) -> list[dict]:
    target_groups = _claim_groups(connection, target, target_summaries)
    source_groups = _claim_groups(connection, source, source_summaries)
    conflicts = []
    for normalized_key in sorted(set(target_groups) & set(source_groups)):
        target_values = target_groups[normalized_key]
        source_values = source_groups[normalized_key]
        differing_values = set(target_values) | set(source_values)
        if len(differing_values) <= 1:
            continue
        target_records = list(target_values.values())
        source_records = list(source_values.values())
        target_value_list = [record["value"] for record in target_records]
        source_value_list = [record["value"] for record in source_records]
        target_source_ids = _unique_ints(
            source_id
            for record in target_records
            for source_id in record["source_message_ids"]
        )
        source_source_ids = _unique_ints(
            source_id
            for record in source_records
            for source_id in record["source_message_ids"]
        )
        subject = target_records[0]["key"] or source_records[0]["key"]
        severity = "high" if normalized_key in {
            "database",
            "model",
            "framework",
            "decision",
            "architecture",
        } else "medium"
        conflicts.append(
            {
                "key": normalized_key,
                "subject": subject,
                "target_values": target_value_list,
                "source_values": source_value_list,
                "target_source_message_ids": target_source_ids,
                "source_source_message_ids": source_source_ids,
                "target_citations": [citation_for_message(i) for i in target_source_ids],
                "source_citations": [citation_for_message(i) for i in source_source_ids],
                "severity": severity,
                "status": "unresolved",
                "resolution_value": None,
            }
        )
    return conflicts


def _preview_token(payload: dict) -> str:
    canonical = {
        key: value
        for key, value in payload.items()
        if key not in {"preview_token", "can_merge", "recommendation"}
    }
    return hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()


def preview_merge(
    connection: sqlite3.Connection,
    conversation_id: int,
    *,
    target_branch_id: int,
    source_branch_id: int,
    target_summary_ids: Optional[list[int]] = None,
    source_summary_ids: Optional[list[int]] = None,
) -> dict:
    target, source, target_path, source_path, common_base = _validate_merge_branches(
        connection,
        conversation_id,
        target_branch_id,
        source_branch_id,
    )
    target_summaries = _validate_merge_summaries(
        connection,
        conversation_id,
        target,
        target_summary_ids or [],
    )
    source_summaries = _validate_merge_summaries(
        connection,
        conversation_id,
        source,
        source_summary_ids or [],
    )
    conflicts = _conflicts_for_branches(
        connection,
        target,
        source,
        target_summaries,
        source_summaries,
    )
    blockers = []
    if not target_summaries:
        blockers.append(
            {
                "code": "target_summary_required",
                "branch_id": target["id"],
                "detail": "Create and select a citable summary for the target branch",
            }
        )
    if not source_summaries:
        blockers.append(
            {
                "code": "source_summary_required",
                "branch_id": source["id"],
                "detail": "Create and select a citable summary for the source branch",
            }
        )
    payload = {
        "conversation_id": conversation_id,
        "target_branch_id": target["id"],
        "source_branch_id": source["id"],
        "target_branch_name": target["name"],
        "source_branch_name": source["name"],
        "target_head_message_id": expected_branch_parent(target),
        "source_head_message_id": expected_branch_parent(source),
        "target_path_message_ids": target_path,
        "source_path_message_ids": source_path,
        "base_message_id": common_base,
        "target_summary_ids": [summary["id"] for summary in target_summaries],
        "source_summary_ids": [summary["id"] for summary in source_summaries],
        "conflicts": conflicts,
        "blockers": blockers,
        "has_conflicts": bool(conflicts),
        "can_merge": not conflicts and not blockers,
        "recommendation": (
            "Create citable branch summaries before merging"
            if blockers
            else "Resolve every conflict explicitly before creating a derived branch"
            if conflicts
            else "Merge remains reversible; original messages will stay unchanged"
        ),
    }
    payload["preview_token"] = _preview_token(payload)
    return payload


def _resolve_conflict(conflict: dict, raw_value: Optional[str]) -> tuple[str, Optional[str]]:
    if raw_value is None:
        return "unresolved", None
    value = str(raw_value).strip()
    normalized = _normalize(value)
    if normalized in {"target", "keep target", "keep_target"}:
        return "resolved", conflict["target_values"][0]
    if normalized in {"source", "keep source", "keep_source"}:
        return "resolved", conflict["source_values"][0]
    if normalized in {"ignore", "ignored"}:
        return "ignored", None
    candidates = conflict["target_values"] + conflict["source_values"]
    for candidate in candidates:
        if _normalize(candidate) == normalized:
            return "resolved", candidate
    raise SummaryError(
        f"Resolution for {conflict['subject']} must choose one of the cited values"
    )


def _merge_claim_inputs(
    connection: sqlite3.Connection,
    target: sqlite3.Row,
    source: sqlite3.Row,
    conflicts: list[dict],
    target_summaries: list[dict],
    source_summaries: list[dict],
) -> tuple[list[dict], list[int], str]:
    target_groups = _claim_groups(connection, target, target_summaries)
    source_groups = _claim_groups(connection, source, source_summaries)
    all_keys = sorted(set(target_groups) | set(source_groups))
    claim_inputs = []
    lines = [
        "Reversible merge snapshot",
        f"- Target branch: {target['name']}",
        f"- Source branch: {source['name']}",
    ]
    for side, summaries in (
        ("Target", target_summaries),
        ("Source", source_summaries),
    ):
        for summary in summaries:
            lines.extend(
                [
                    "",
                    f"{side} summary #{summary['id']}: {summary['title']}",
                    _short_text(summary["content"], 4_000),
                ]
            )
    conflict_by_key = {conflict["key"]: conflict for conflict in conflicts}
    selected_source_ids = _unique_ints(
        branch_path_ids(connection, target) + branch_path_ids(connection, source)
    )
    for key in all_keys:
        records = []
        if key in conflict_by_key:
            conflict = conflict_by_key[key]
            if conflict.get("status") == "ignored":
                for value in conflict["target_values"] + conflict["source_values"]:
                    lines.append(
                        f"- {conflict['subject']}: {value} (kept as unresolved evidence) "
                        + " ".join(
                            citation_for_message(message_id)
                            for message_id in (
                                conflict["target_source_message_ids"]
                                if value in conflict["target_values"]
                                else conflict["source_source_message_ids"]
                            )
                        )
                    )
                continue
            chosen = conflict.get("resolution_value")
            for side_groups in (target_groups, source_groups):
                record = side_groups.get(key, {}).get(_normalize(chosen or ""))
                if record is not None:
                    records.append(record)
        else:
            records.extend(target_groups.get(key, {}).values())
            records.extend(source_groups.get(key, {}).values())

        seen_values = set()
        for record in records:
            normalized_value = _normalize(record["value"])
            if normalized_value in seen_values:
                continue
            seen_values.add(normalized_value)
            source_id = int(record["source_message_ids"][0])
            citations = " ".join(record["citations"])
            lines.append(f"- {record['key']}: {record['value']} {citations}")
            claim_inputs.append(
                {
                    "key": record["key"],
                    "value": record["value"],
                    "claim_text": record["claim_text"],
                    "source_message_id": source_id,
                }
            )
    lines.append("")
    lines.append(
        "Original evidence: "
        + " ".join(citation_for_message(message_id) for message_id in selected_source_ids)
    )
    return claim_inputs, selected_source_ids, "\n".join(lines)


def _next_merge_version(connection: sqlite3.Connection, conversation_id: int) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM merge_operations "
        "WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return int(row["version"]) + 1


def _merge_conflict_payloads(
    connection: sqlite3.Connection,
    merge_id: int,
) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM merge_conflicts WHERE merge_id = ? ORDER BY id ASC",
        (merge_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "key": row["conflict_key"],
            "subject": row["subject"],
            "target_values": _json_load(row["target_values_json"], []),
            "source_values": _json_load(row["source_values_json"], []),
            "target_source_message_ids": _json_load(row["target_source_ids_json"], []),
            "source_source_message_ids": _json_load(row["source_source_ids_json"], []),
            "severity": row["severity"],
            "status": row["status"],
            "resolution_value": row["resolution_value"],
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
        }
        for row in rows
    ]


def merge_payload(connection: sqlite3.Connection, merge_id: int) -> dict:
    row = connection.execute(
        "SELECT * FROM merge_operations WHERE id = ?", (merge_id,)
    ).fetchone()
    if row is None:
        raise RecordNotFoundError("Merge", merge_id)
    payload = dict(row)
    payload["resolution"] = _json_load(row["resolution_json"], {})
    payload["conflicts"] = _merge_conflict_payloads(connection, merge_id)
    payload["events"] = [
        {
            "id": event["id"],
            "event_type": event["event_type"],
            "payload": _json_load(event["payload_json"], {}),
            "created_at": event["created_at"],
        }
        for event in connection.execute(
            "SELECT * FROM merge_events WHERE merge_id = ? ORDER BY id ASC",
            (merge_id,),
        ).fetchall()
    ]
    for field in ("target_branch_id", "source_branch_id", "result_branch_id"):
        branch_id = row[field]
        branch = None
        if branch_id is not None:
            branch = connection.execute(
                "SELECT * FROM branches WHERE id = ?", (branch_id,)
            ).fetchone()
        payload[field.replace("_id", "")] = (
            row_to_branch(branch, branch_path_ids(connection, branch))
            if branch is not None
            else None
        )
    snapshot_id = row["snapshot_summary_id"]
    payload["snapshot_summary"] = (
        summary_payload(connection, get_summary(connection, snapshot_id))
        if snapshot_id is not None
        and connection.execute(
            "SELECT 1 FROM summaries WHERE id = ?", (snapshot_id,)
        ).fetchone()
        else None
    )
    return payload


def execute_merge(
    connection: sqlite3.Connection,
    conversation_id: int,
    *,
    target_branch_id: int,
    source_branch_id: int,
    target_summary_ids: Optional[list[int]] = None,
    source_summary_ids: Optional[list[int]] = None,
    preview_token: Optional[str] = None,
    resolutions: Optional[dict[str, str]] = None,
    name: Optional[str] = None,
    activate: bool = False,
) -> dict:
    if not preview_token:
        raise MergePreviewRequiredError()
    preview = preview_merge(
        connection,
        conversation_id,
        target_branch_id=target_branch_id,
        source_branch_id=source_branch_id,
        target_summary_ids=target_summary_ids,
        source_summary_ids=source_summary_ids,
    )
    if preview_token and preview_token != preview["preview_token"]:
        raise MergePreviewStaleError(preview["preview_token"], preview_token)
    if preview["blockers"]:
        raise MergePreconditionError(preview["blockers"], preview)

    requested_resolutions = resolutions or {}
    resolved_conflicts = []
    unresolved_conflicts = []
    normalized_resolutions = {
        _normalize(key): value for key, value in requested_resolutions.items()
    }
    for conflict in preview["conflicts"]:
        status, value = _resolve_conflict(
            conflict,
            normalized_resolutions.get(conflict["key"]),
        )
        conflict["status"] = status
        conflict["resolution_value"] = value
        if status == "unresolved":
            unresolved_conflicts.append(conflict)
        else:
            resolved_conflicts.append(conflict)
    if unresolved_conflicts:
        preview["conflicts"] = preview["conflicts"]
        raise MergeConflictError(unresolved_conflicts, preview)

    target, source, _, _, common_base = _validate_merge_branches(
        connection,
        conversation_id,
        target_branch_id,
        source_branch_id,
    )
    target_summaries = _validate_merge_summaries(
        connection,
        conversation_id,
        target,
        target_summary_ids or [],
    )
    source_summaries = _validate_merge_summaries(
        connection,
        conversation_id,
        source,
        source_summary_ids or [],
    )
    resolution_values = {
        conflict["key"]: conflict["resolution_value"]
        for conflict in resolved_conflicts
    }
    conversation = get_conversation(connection, conversation_id)
    previous_active_branch_id = conversation["active_branch_id"]
    previous_active_message_id = conversation["active_message_id"]
    claim_inputs, source_ids, snapshot_content = _merge_claim_inputs(
        connection,
        target,
        source,
        preview["conflicts"],
        target_summaries,
        source_summaries,
    )
    result_name = (name or "").strip() or (
        f"Merge {source['name']} into {target['name']}"
    )
    result_branch = create_merge_branch(
        connection,
        conversation_id,
        target,
        common_base,
        expected_branch_parent(target),
        result_name,
    )
    timestamp = utc_now()
    version = _next_merge_version(connection, conversation_id)
    cursor = connection.execute(
        """
        INSERT INTO merge_operations (
            conversation_id, target_branch_id, source_branch_id,
            result_branch_id, base_message_id, target_branch_name,
            source_branch_name, result_branch_name, version, preview_token,
            status, resolution_json, created_at,
            previous_active_branch_id, previous_active_message_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)
        """,
        (
            conversation_id,
            target["id"],
            source["id"],
            result_branch["id"],
            common_base,
            target["name"],
            source["name"],
            result_branch["name"],
            version,
            preview["preview_token"],
            _json(resolution_values),
            timestamp,
            previous_active_branch_id,
            previous_active_message_id,
        ),
    )
    merge_id = int(cursor.lastrowid)
    source_branch_by_message = {}
    for message_id in branch_path_ids(connection, target):
        source_branch_by_message[message_id] = target["id"]
    for message_id in branch_path_ids(connection, source):
        source_branch_by_message.setdefault(message_id, source["id"])
    snapshot = create_summary(
        connection,
        conversation_id,
        branch_id=result_branch["id"],
        anchor_message_id=expected_branch_parent(target),
        title=f"Merge snapshot v{version}",
        content=snapshot_content,
        source_message_ids=source_ids,
        claims=claim_inputs,
        kind="merge_snapshot",
        source_branch_by_message=source_branch_by_message,
    )
    connection.execute(
        "UPDATE merge_operations SET snapshot_summary_id = ? WHERE id = ?",
        (snapshot["id"], merge_id),
    )
    for side, summary_ids in (
        ("target", target_summary_ids or []),
        ("source", source_summary_ids or []),
    ):
        for source_order, summary_id in enumerate(_unique_ints(summary_ids)):
            connection.execute(
                """
                INSERT INTO merge_operation_summaries (
                    merge_id, summary_id, side, source_order
                )
                VALUES (?, ?, ?, ?)
                """,
                (merge_id, summary_id, side, source_order),
            )
    connection.execute(
        """
        INSERT INTO merge_operation_summaries (
            merge_id, summary_id, side, source_order
        )
        VALUES (?, ?, 'derived', 0)
        """,
        (merge_id, snapshot["id"]),
    )
    for conflict in preview["conflicts"]:
        status = conflict.get("status", "resolved")
        connection.execute(
            """
            INSERT INTO merge_conflicts (
                merge_id, conflict_key, subject, target_values_json,
                source_values_json, target_source_ids_json,
                source_source_ids_json, severity, status, resolution_value,
                created_at, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merge_id,
                conflict["key"],
                conflict["subject"],
                _json(conflict["target_values"]),
                _json(conflict["source_values"]),
                _json(conflict["target_source_message_ids"]),
                _json(conflict["source_source_message_ids"]),
                conflict["severity"],
                "ignored" if status == "ignored" else "resolved",
                conflict.get("resolution_value"),
                timestamp,
                timestamp,
            ),
        )
    connection.execute(
        """
        INSERT INTO merge_events (merge_id, event_type, payload_json, created_at)
        VALUES (?, 'created', ?, ?)
        """,
        (
            merge_id,
            _json(
                {
                    "preview_token": preview["preview_token"],
                    "target_branch_id": target["id"],
                    "source_branch_id": source["id"],
                    "result_branch_id": result_branch["id"],
                    "snapshot_summary_id": snapshot["id"],
                    "resolutions": resolution_values,
                }
            ),
            timestamp,
        ),
    )
    if activate:
        set_active_state(
            connection,
            conversation_id,
            result_branch["id"],
            expected_branch_parent(result_branch),
        )
    return merge_payload(connection, merge_id)


def rollback_merge(
    connection: sqlite3.Connection,
    merge_id: int,
    *,
    reason: Optional[str] = None,
) -> dict:
    row = connection.execute(
        "SELECT * FROM merge_operations WHERE id = ?", (merge_id,)
    ).fetchone()
    if row is None:
        raise RecordNotFoundError("Merge", merge_id)
    if row["status"] != "completed":
        raise SummaryError("Merge has already been rolled back")
    timestamp = utc_now()
    rollback_reason = (reason or "").strip() or "User requested rollback"
    connection.execute(
        """
        UPDATE merge_operations
        SET status = 'rolled_back', rolled_back_at = ?, rollback_reason = ?
        WHERE id = ?
        """,
        (timestamp, rollback_reason, merge_id),
    )
    target_branch = (
        connection.execute(
            "SELECT * FROM branches WHERE id = ?", (row["target_branch_id"],)
        ).fetchone()
        if row["target_branch_id"] is not None
        else None
    )
    if target_branch is None:
        target_branch = connection.execute(
            """
            SELECT * FROM branches
            WHERE conversation_id = ? AND is_main = 1
            """,
            (row["conversation_id"],),
        ).fetchone()
    conversation = get_conversation(connection, row["conversation_id"])
    restored_branch = (
        connection.execute(
            "SELECT * FROM branches WHERE id = ?",
            (row["previous_active_branch_id"],),
        ).fetchone()
        if row["previous_active_branch_id"] is not None
        else None
    ) or target_branch
    if restored_branch is None:
        restored_branch = connection.execute(
            """
            SELECT * FROM branches
            WHERE conversation_id = ? AND is_main = 1
            """,
            (row["conversation_id"],),
        ).fetchone()
    restored_message_id = row["previous_active_message_id"]
    if restored_branch is not None:
        visible_ids = set(branch_path_ids(connection, restored_branch))
        if restored_message_id not in visible_ids:
            restored_message_id = expected_branch_parent(restored_branch)
    if restored_branch is not None:
        set_active_state(
            connection,
            row["conversation_id"],
            restored_branch["id"],
            restored_message_id,
        )
    connection.execute(
        """
        INSERT INTO merge_events (merge_id, event_type, payload_json, created_at)
        VALUES (?, 'rollback', ?, ?)
        """,
        (
            merge_id,
            _json(
                {
                    "reason": rollback_reason,
                    "restored_branch_id": restored_branch["id"] if restored_branch else None,
                    "restored_message_id": restored_message_id,
                }
            ),
            timestamp,
        ),
    )
    return merge_payload(connection, merge_id)


def list_merges(connection: sqlite3.Connection, conversation_id: int) -> list[dict]:
    get_conversation(connection, conversation_id)
    rows = connection.execute(
        """
        SELECT id FROM merge_operations
        WHERE conversation_id = ?
        ORDER BY version DESC, id DESC
        """,
        (conversation_id,),
    ).fetchall()
    return [merge_payload(connection, row["id"]) for row in rows]


def _dag_node(node_id: str, node_type: str, label: str, **data: Any) -> dict:
    return {"id": node_id, "type": node_type, "label": label, **data}


def build_dag(connection: sqlite3.Connection, conversation_id: int) -> dict:
    get_conversation(connection, conversation_id)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_keys = set()

    def add_edge(source: str, target: str, relation: str) -> None:
        key = (source, target, relation)
        if key in edge_keys:
            return
        edge_keys.add(key)
        edges.append(
            {
                "id": f"{source}->{target}:{relation}",
                "source": source,
                "target": target,
                "relation": relation,
            }
        )

    messages = connection.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    ).fetchall()
    for row in messages:
        nodes[f"message:{row['id']}"] = _dag_node(
            f"message:{row['id']}",
            "message",
            f"{row['role']} #{row['id']}",
            message_id=row["id"],
            role=row["role"],
            content=_short_text(row["content"], 180),
            created_at=row["created_at"],
        )
        if row["parent_id"] is not None:
            add_edge(
                f"message:{row['parent_id']}",
                f"message:{row['id']}",
                "message_parent",
            )

    branches = connection.execute(
        "SELECT * FROM branches WHERE conversation_id = ? ORDER BY is_main DESC, id ASC",
        (conversation_id,),
    ).fetchall()
    branch_merge_ids = {
        row["result_branch_id"]: row["id"]
        for row in connection.execute(
            "SELECT id, result_branch_id FROM merge_operations "
            "WHERE conversation_id = ? AND result_branch_id IS NOT NULL",
            (conversation_id,),
        ).fetchall()
    }
    for row in branches:
        merge_id = branch_merge_ids.get(row["id"])
        nodes[f"branch:{row['id']}"] = _dag_node(
            f"branch:{row['id']}",
            "branch",
            row["name"],
            branch_id=row["id"],
            is_main=bool(row["is_main"]),
            head_message_id=expected_branch_parent(row),
            path_message_ids=branch_path_ids(connection, row),
            origin_merge_id=merge_id,
        )
        head_message_id = expected_branch_parent(row)
        if head_message_id is not None and f"message:{head_message_id}" in nodes:
            add_edge(f"message:{head_message_id}", f"branch:{row['id']}", "branch_head")

    summary_rows = connection.execute(
        "SELECT * FROM summaries WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    ).fetchall()
    for row in summary_rows:
        payload = summary_payload(connection, row, include_claims=False)
        nodes[f"summary:{row['id']}"] = _dag_node(
            f"summary:{row['id']}",
            "summary",
            row["title"],
            summary_id=row["id"],
            branch_id=row["branch_id"],
            version=row["version"],
            status=row["status"],
            citation_count=payload["citation_count"],
            is_citable=payload["is_citable"],
        )
        for source in payload["sources"]:
            message_node = f"message:{source['message_id']}"
            if message_node in nodes:
                add_edge(message_node, f"summary:{row['id']}", "cites")
        if row["kind"] == "branch" and row["branch_id"] is not None:
            branch_node = f"branch:{row['branch_id']}"
            if branch_node in nodes:
                add_edge(branch_node, f"summary:{row['id']}", "summarizes")

    merge_rows = connection.execute(
        "SELECT id FROM merge_operations WHERE conversation_id = ? ORDER BY version ASC, id ASC",
        (conversation_id,),
    ).fetchall()
    history = []
    for merge_row in merge_rows:
        merge = merge_payload(connection, merge_row["id"])
        merge_node = f"merge:{merge['id']}"
        nodes[merge_node] = _dag_node(
            merge_node,
            "merge",
            merge["result_branch_name"] or f"Merge v{merge['version']}",
            merge_id=merge["id"],
            version=merge["version"],
            status=merge["status"],
            target_branch_id=merge["target_branch_id"],
            source_branch_id=merge["source_branch_id"],
            result_branch_id=merge["result_branch_id"],
            base_message_id=merge["base_message_id"],
            created_at=merge["created_at"],
            rolled_back_at=merge["rolled_back_at"],
        )
        for field in ("target_branch_id", "source_branch_id"):
            branch_id = merge[field]
            if branch_id is not None and f"branch:{branch_id}" in nodes:
                add_edge(f"branch:{branch_id}", merge_node, "merge_input")
        for link in connection.execute(
            "SELECT summary_id, side FROM merge_operation_summaries "
            "WHERE merge_id = ? ORDER BY side, source_order",
            (merge["id"],),
        ).fetchall():
            summary_node = f"summary:{link['summary_id']}"
            if summary_node in nodes:
                if link["side"] == "derived":
                    add_edge(merge_node, summary_node, "creates_summary")
                else:
                    add_edge(summary_node, merge_node, "merge_context")
        if merge["result_branch_id"] is not None:
            result_node = f"branch:{merge['result_branch_id']}"
            if result_node in nodes:
                add_edge(merge_node, result_node, "creates")
        history.append(merge)

    for branch in branches:
        branch_node = f"branch:{branch['id']}"
        if branch_node not in nodes or branch_merge_ids.get(branch["id"]) is not None:
            continue
        fork_message_id = branch["forked_from_message_id"]
        if fork_message_id is not None and f"message:{fork_message_id}" in nodes:
            add_edge(f"message:{fork_message_id}", branch_node, "forks")

    indegree = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge["source"] in indegree and edge["target"] in indegree:
            indegree[edge["target"]] += 1
            outgoing[edge["source"]].append(edge["target"])
    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        node_id = queue.pop()
        visited += 1
        for target_id in outgoing[node_id]:
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                queue.append(target_id)

    return {
        "conversation_id": conversation_id,
        "nodes": list(nodes.values()),
        "edges": edges,
        "history": history,
        "is_acyclic": visited == len(nodes),
    }
