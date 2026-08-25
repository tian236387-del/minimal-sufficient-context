from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import mimetypes
import os
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


STUDY_DIR = Path(__file__).resolve().parent
STATIC_DIR = STUDY_DIR / "static"
DEFAULT_TASKS_PATH = STUDY_DIR / "tasks.json"
DEFAULT_DATA_DIR = STUDY_DIR / "data"
DOMAIN_ORDERS = tuple(itertools.permutations(("programming", "research", "writing")))
CONDITIONS = ("branch", "linear")
ALLOWED_EVENTS = {
    "answer_edited",
    "draft_failed",
    "draft_received",
    "draft_requested",
    "page_hidden",
    "session_started",
    "task_opened",
    "task_submitted",
}
PROFILE_VALUES = {
    "experience": {"beginner", "intermediate", "advanced"},
    "primary_domain": {"programming", "research", "writing", "mixed"},
    "ai_frequency": {"rarely", "monthly", "weekly", "daily"},
}
RATING_FIELDS = ("confidence", "workload", "usability", "trust")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StudyError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = int(status)


def canonical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace() and character not in {",", "，"}
    )


def detect_terms(answer: str, terms: list[str]) -> list[str]:
    normalized_answer = canonical_text(answer)
    return [term for term in terms if canonical_text(term) in normalized_answer]


def estimate_tokens(messages: list[dict]) -> int:
    total = 2
    for message in messages:
        content = message.get("content", "")
        ascii_chars = sum(1 for character in content if ord(character) < 128)
        non_ascii_chars = len(content) - ascii_chars
        total += 4 + math.ceil(ascii_chars / 4) + non_ascii_chars
    return total


def load_task_pack(path: Path = DEFAULT_TASKS_PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 6:
        raise ValueError("Human study requires exactly six tasks")
    task_ids = {task.get("id") for task in tasks}
    if len(task_ids) != len(tasks) or None in task_ids:
        raise ValueError("Task ids must be present and unique")
    pairs = Counter((task.get("domain"), task.get("variant")) for task in tasks)
    expected_pairs = {
        (domain, variant)
        for domain in ("programming", "research", "writing")
        for variant in ("A", "B")
    }
    if set(pairs) != expected_pairs or set(pairs.values()) != {1}:
        raise ValueError("Each domain requires one A and one B task")
    for task in tasks:
        branches = task.get("branches", [])
        branch_ids = {branch.get("id") for branch in branches}
        if len(branch_ids) < 3 or None in branch_ids:
            raise ValueError(f"Task {task['id']} needs at least three branches")
        if task.get("active_branch_id") not in branch_ids:
            raise ValueError(f"Task {task['id']} has an invalid active branch")
        if set(task.get("linear_order", [])) != branch_ids:
            raise ValueError(f"Task {task['id']} has an invalid linear order")
        if not task.get("expected_terms") or not task.get("forbidden_terms"):
            raise ValueError(f"Task {task['id']} needs scoring terms")
    return data


def task_pack_fingerprint(task_pack: dict) -> str:
    source = json.dumps(
        task_pack,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def build_assignment(tasks: list[dict], cell: int) -> list[dict]:
    if not 0 <= cell < len(DOMAIN_ORDERS) * len(CONDITIONS):
        raise ValueError("Counterbalance cell is out of range")
    domain_order = DOMAIN_ORDERS[cell // len(CONDITIONS)]
    first_condition = CONDITIONS[cell % len(CONDITIONS)]
    task_lookup = {(task["domain"], task["variant"]): task for task in tasks}
    assignments = []
    occurrences = Counter()
    for index, domain in enumerate(domain_order + domain_order):
        variant = "A" if occurrences[domain] == 0 else "B"
        occurrences[domain] += 1
        condition = CONDITIONS[(CONDITIONS.index(first_condition) + index) % 2]
        task = task_lookup[(domain, variant)]
        assignments.append(
            {
                "order": index + 1,
                "task_id": task["id"],
                "domain": domain,
                "variant": variant,
                "condition": condition,
                "title": task["title"],
            }
        )
    return assignments


def compile_messages(
    task: dict,
    condition: str,
    participant_prompt: str,
    prior_turns: list[dict] | None = None,
) -> list[dict]:
    if condition not in CONDITIONS:
        raise ValueError(f"Unknown condition: {condition}")
    branches = {branch["id"]: branch for branch in task["branches"]}
    active = branches[task["active_branch_id"]]
    messages = [
        {
            "role": "system",
            "content": (
                "你正在完成一项封闭资料任务。只能使用给定资料；以当前活动分支的最终决定为准，"
                "不得借用、猜测或混合其他分支的事实。输出应直接满足交付要求。"
            ),
        },
        *task["shared_context"],
    ]
    if condition == "branch":
        messages.extend(active["history"])
    else:
        for branch_id in task["linear_order"]:
            branch = branches[branch_id]
            marker = " [ACTIVE]" if branch_id == task["active_branch_id"] else ""
            messages.append(
                {
                    "role": "user",
                    "content": f"[Branch: {branch['name']}{marker}]",
                }
            )
            for item in branch["history"]:
                messages.append(
                    {
                        "role": item["role"],
                        "content": f"[{branch['name']}] {item['content']}",
                    }
                )
    for turn in prior_turns or []:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append(
        {
            "role": "user",
            "content": (
                f"当前活动分支：{active['name']}\n"
                f"任务：{task['brief']}\n"
                f"交付：{task['deliverable']}\n"
                f"参与者请求：{participant_prompt}"
            ),
        }
    )
    return messages


@dataclass(frozen=True, slots=True)
class ModelSettings:
    model: str
    base_url: str
    num_ctx: int
    num_predict: int
    temperature: float
    timeout_seconds: int


class OllamaClient:
    def __init__(self, settings: ModelSettings) -> None:
        self.settings = settings

    def health(self) -> dict:
        request = urllib.request.Request(f"{self.settings.base_url}/api/tags")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            return {"available": False, "model_installed": False, "error": str(error)}
        models = {
            item.get("name"): item
            for item in payload.get("models", [])
            if isinstance(item, dict)
        }
        selected = models.get(self.settings.model)
        return {
            "available": True,
            "model_installed": selected is not None,
            "model_digest": selected.get("digest") if selected else None,
            "model_details": selected.get("details") if selected else None,
            "error": None,
        }

    def chat(self, messages: list[dict], seed: int) -> dict:
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "num_ctx": self.settings.num_ctx,
                "num_predict": self.settings.num_predict,
                "temperature": self.settings.temperature,
                "seed": seed,
            },
        }
        request = urllib.request.Request(
            f"{self.settings.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.settings.timeout_seconds,
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise StudyError(
                f"Ollama returned HTTP {error.code}: {body[:300]}",
                HTTPStatus.BAD_GATEWAY,
            ) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise StudyError(
                f"Ollama request failed: {error}",
                HTTPStatus.BAD_GATEWAY,
            ) from error
        answer = result.get("message", {}).get("content", "").strip()
        if not answer:
            raise StudyError("Ollama returned an empty answer", HTTPStatus.BAD_GATEWAY)
        return {
            "answer": answer,
            "model": self.settings.model,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "prompt_tokens": result.get("prompt_eval_count"),
            "response_tokens": result.get("eval_count"),
        }


class StudyStore:
    def __init__(
        self,
        task_pack: dict,
        data_dir: Path,
        runtime: dict | None = None,
    ) -> None:
        self.study = task_pack["study"]
        self.tasks = task_pack["tasks"]
        self.task_pack_sha256 = task_pack_fingerprint(task_pack)
        self.runtime = runtime or {}
        self.tasks_by_id = {task["id"]: task for task in self.tasks}
        self.data_dir = data_dir
        self.sessions_path = data_dir / "sessions.jsonl"
        self.events_path = data_dir / "events.jsonl"
        self.results_path = data_dir / "results.jsonl"
        self.completions_path = data_dir / "completions.jsonl"
        self._lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions = {
            row["session_id"]: row for row in self._read_jsonl(self.sessions_path)
        }
        self.completed_tasks = {
            (row["session_id"], row["task_id"])
            for row in self._read_jsonl(self.results_path)
        }
        self.completed_sessions = {
            row["session_id"] for row in self._read_jsonl(self.completions_path)
        }
        self.generation_counts = Counter(
            (row.get("session_id"), row.get("task_id"))
            for row in self._read_jsonl(self.events_path)
            if row.get("event_type") == "draft_received"
        )

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSONL in {path}:{line_number}") from error
        return rows

    @staticmethod
    def _append_jsonl(path: Path, row: dict) -> None:
        with path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

    def _require_session(self, session_id: str) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise StudyError("Unknown session", HTTPStatus.NOT_FOUND)
        return session

    @staticmethod
    def _validate_profile(profile: Any) -> dict:
        if not isinstance(profile, dict):
            raise StudyError("profile must be an object")
        validated = {}
        for field, values in PROFILE_VALUES.items():
            value = profile.get(field)
            if value not in values:
                raise StudyError(f"Invalid profile field: {field}")
            validated[field] = value
        return validated

    def create_session(self, consent: bool, profile: dict) -> dict:
        if consent is not True:
            raise StudyError("Consent is required")
        validated_profile = self._validate_profile(profile)
        with self._lock:
            cell_counts = Counter(row["counterbalance_cell"] for row in self.sessions.values())
            cell = min(range(12), key=lambda value: (cell_counts[value], value))
            session_id = uuid.uuid4().hex
            session = {
                "session_id": session_id,
                "study_id": self.study["id"],
                "study_version": self.study["version"],
                "task_pack_sha256": self.task_pack_sha256,
                "runtime": self.runtime,
                "counterbalance_cell": cell,
                "profile": validated_profile,
                "assignment": build_assignment(self.tasks, cell),
                "created_at": utc_now(),
            }
            self._append_jsonl(self.sessions_path, session)
            self.sessions[session_id] = session
        return self.public_session(session_id)

    def public_session(self, session_id: str) -> dict:
        session = self._require_session(session_id)
        assignments = []
        for assignment in session["assignment"]:
            task = self.tasks_by_id[assignment["task_id"]]
            assignments.append(
                {
                    **assignment,
                    "completed": (session_id, task["id"]) in self.completed_tasks,
                    "task": self.public_task(task, assignment["condition"]),
                }
            )
        return {
            "session_id": session_id,
            "study": self.study,
            "counterbalance_cell": session["counterbalance_cell"],
            "assignment": assignments,
            "completed": session_id in self.completed_sessions,
        }

    @staticmethod
    def public_task(task: dict, condition: str) -> dict:
        branch_lookup = {branch["id"]: branch for branch in task["branches"]}
        branches = [branch_lookup[branch_id] for branch_id in task["linear_order"]]
        if condition == "branch":
            branches = [
                branch
                for branch in branches
                if branch["id"] == task["active_branch_id"]
            ]
        return {
            key: task[key]
            for key in (
                "id",
                "domain",
                "title",
                "active_branch_id",
                "brief",
                "deliverable",
                "shared_context",
            )
        } | {"branches": branches, "condition": condition}

    def assignment_for(self, session_id: str, task_id: str) -> tuple[dict, dict]:
        session = self._require_session(session_id)
        assignment = next(
            (item for item in session["assignment"] if item["task_id"] == task_id),
            None,
        )
        if assignment is None:
            raise StudyError("Task is not assigned to this session", HTTPStatus.FORBIDDEN)
        return assignment, self.tasks_by_id[task_id]

    def record_event(
        self,
        session_id: str,
        event_type: str,
        task_id: str | None = None,
        elapsed_ms: int | None = None,
        payload: dict | None = None,
    ) -> dict:
        self._require_session(session_id)
        if event_type not in ALLOWED_EVENTS:
            raise StudyError("Invalid event type")
        if task_id is not None:
            self.assignment_for(session_id, task_id)
        if elapsed_ms is not None and (not isinstance(elapsed_ms, int) or elapsed_ms < 0):
            raise StudyError("elapsed_ms must be a non-negative integer")
        payload = payload or {}
        if not isinstance(payload, dict):
            raise StudyError("event payload must be an object")
        if len(json.dumps(payload, ensure_ascii=False)) > 100_000:
            raise StudyError("event payload is too large")
        row = {
            "event_id": uuid.uuid4().hex,
            "session_id": session_id,
            "task_id": task_id,
            "event_type": event_type,
            "elapsed_ms": elapsed_ms,
            "payload": payload,
            "created_at": utc_now(),
        }
        with self._lock:
            self._append_jsonl(self.events_path, row)
            if event_type == "draft_received":
                self.generation_counts[(session_id, task_id)] += 1
        return row

    def reserve_generation(self, session_id: str, task_id: str) -> tuple[dict, dict]:
        assignment, task = self.assignment_for(session_id, task_id)
        if (session_id, task_id) in self.completed_tasks:
            raise StudyError("Task is already completed", HTTPStatus.CONFLICT)
        if self.generation_counts[(session_id, task_id)] >= 4:
            raise StudyError("Draft limit reached for this task", HTTPStatus.TOO_MANY_REQUESTS)
        return assignment, task

    def submit_result(self, body: dict) -> dict:
        session_id = body.get("session_id", "")
        task_id = body.get("task_id", "")
        assignment, task = self.assignment_for(session_id, task_id)
        final_answer = body.get("final_answer")
        if not isinstance(final_answer, str) or not 40 <= len(final_answer.strip()) <= 20_000:
            raise StudyError("final_answer must contain 40-20000 characters")
        ratings = body.get("ratings")
        if not isinstance(ratings, dict):
            raise StudyError("ratings must be an object")
        for field in RATING_FIELDS:
            value = ratings.get(field)
            if not isinstance(value, int) or not 1 <= value <= 7:
                raise StudyError(f"Rating {field} must be an integer from 1 to 7")
        elapsed_ms = body.get("elapsed_ms")
        active_ms = body.get("active_ms")
        for name, value in (("elapsed_ms", elapsed_ms), ("active_ms", active_ms)):
            if not isinstance(value, int) or value < 0:
                raise StudyError(f"{name} must be a non-negative integer")
        if self.generation_counts[(session_id, task_id)] < 1:
            raise StudyError("请先生成至少一份 AI 草稿再提交")
        expected_hits = detect_terms(final_answer, task["expected_terms"])
        forbidden_hits = detect_terms(final_answer, task["forbidden_terms"])
        row = {
            "result_id": uuid.uuid4().hex,
            "session_id": session_id,
            "task_id": task_id,
            "domain": task["domain"],
            "variant": task["variant"],
            "condition": assignment["condition"],
            "order": assignment["order"],
            "final_answer": final_answer.strip(),
            "answer_sha256": hashlib.sha256(final_answer.strip().encode("utf-8")).hexdigest(),
            "answer_chars": len(final_answer.strip()),
            "expected_hits": expected_hits,
            "expected_coverage": round(len(expected_hits) / len(task["expected_terms"]), 4),
            "forbidden_hits": forbidden_hits,
            "contaminated": bool(forbidden_hits),
            "ratings": {field: ratings[field] for field in RATING_FIELDS},
            "elapsed_ms": elapsed_ms,
            "active_ms": active_ms,
            "draft_count": self.generation_counts[(session_id, task_id)],
            "task_pack_sha256": self.task_pack_sha256,
            "runtime": self.runtime,
            "created_at": utc_now(),
        }
        with self._lock:
            key = (session_id, task_id)
            if key in self.completed_tasks:
                raise StudyError("Task is already completed", HTTPStatus.CONFLICT)
            self._append_jsonl(self.results_path, row)
            self.completed_tasks.add(key)
        return {"result_id": row["result_id"], "accepted": True}

    def complete_session(self, body: dict) -> dict:
        session_id = body.get("session_id", "")
        self._require_session(session_id)
        if session_id in self.completed_sessions:
            raise StudyError("Session is already completed", HTTPStatus.CONFLICT)
        assigned = {
            item["task_id"] for item in self.sessions[session_id]["assignment"]
        }
        completed = {
            task_id
            for current_session, task_id in self.completed_tasks
            if current_session == session_id
        }
        if completed != assigned:
            raise StudyError("All assigned tasks must be completed first")
        preference = body.get("preference")
        if preference not in {"branch", "linear", "no_preference"}:
            raise StudyError("Invalid preference")
        comments = body.get("comments", "")
        if not isinstance(comments, str) or len(comments) > 2_000:
            raise StudyError("comments must be at most 2000 characters")
        difference = body.get("perceived_difference")
        if not isinstance(difference, int) or not 1 <= difference <= 7:
            raise StudyError("perceived_difference must be 1-7")
        row = {
            "session_id": session_id,
            "preference": preference,
            "perceived_difference": difference,
            "comments": comments.strip(),
            "created_at": utc_now(),
        }
        with self._lock:
            self._append_jsonl(self.completions_path, row)
            self.completed_sessions.add(session_id)
        return {"accepted": True}


class StudyApplication:
    def __init__(self, store: StudyStore, ollama: OllamaClient) -> None:
        self.store = store
        self.ollama = ollama

    def generate(self, body: dict) -> dict:
        session_id = body.get("session_id", "")
        task_id = body.get("task_id", "")
        assignment, task = self.store.reserve_generation(session_id, task_id)
        prompt = body.get("prompt", "")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 8_000:
            raise StudyError("prompt must contain 1-8000 characters")
        prior_turns = body.get("prior_turns", [])
        if not isinstance(prior_turns, list) or len(prior_turns) > 8:
            raise StudyError("prior_turns must be a list with at most 8 items")
        clean_turns = []
        for turn in prior_turns:
            if not isinstance(turn, dict) or turn.get("role") not in {"user", "assistant"}:
                raise StudyError("Invalid prior turn")
            content = turn.get("content")
            if not isinstance(content, str) or len(content) > 12_000:
                raise StudyError("Invalid prior turn content")
            clean_turns.append({"role": turn["role"], "content": content})
        messages = compile_messages(
            task,
            assignment["condition"],
            prompt.strip(),
            clean_turns,
        )
        self.store.record_event(
            session_id,
            "draft_requested",
            task_id,
            payload={
                "condition": assignment["condition"],
                "prompt": prompt.strip(),
                "context_messages": len(messages),
                "estimated_tokens": estimate_tokens(messages),
            },
        )
        try:
            result = self.ollama.chat(messages, seed=task["seed"])
        except StudyError as error:
            self.store.record_event(
                session_id,
                "draft_failed",
                task_id,
                payload={"error": str(error)},
            )
            raise
        self.store.record_event(
            session_id,
            "draft_received",
            task_id,
            payload={
                **result,
                "participant_prompt": prompt.strip(),
                "context_messages": len(messages),
                "estimated_tokens": estimate_tokens(messages),
            },
        )
        return {
            **result,
            "condition": assignment["condition"],
            "context_messages": len(messages),
            "estimated_tokens": estimate_tokens(messages),
        }


class StudyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, application: StudyApplication) -> None:
        super().__init__(address, handler)
        self.application = application


class StudyRequestHandler(BaseHTTPRequestHandler):
    server: StudyHTTPServer
    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def log_message(self, format_string: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def _send_bytes(
        self,
        status: int,
        content: bytes,
        content_type: str,
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:",
            )
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def _send_json(self, status: int, payload: dict) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, content, "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise StudyError("Content-Length is required", HTTPStatus.LENGTH_REQUIRED)
        try:
            length = int(raw_length)
        except ValueError as error:
            raise StudyError("Invalid Content-Length") from error
        if length < 0 or length > 2_000_000:
            raise StudyError("Request body is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StudyError("Request body must be valid UTF-8 JSON") from error
        if not isinstance(body, dict):
            raise StudyError("Request body must be an object")
        return body

    def _handle_error(self, error: Exception) -> None:
        if isinstance(error, StudyError):
            self._send_json(error.status, {"error": str(error)})
            return
        print(f"Unhandled request error: {error!r}")
        self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"})

    def do_GET(self) -> None:
        try:
            path = unquote(urlparse(self.path).path)
            if path == "/api/health":
                health = self.server.application.ollama.health()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "study": self.server.application.store.study,
                        "model": self.server.application.ollama.settings.model,
                        "ollama": health,
                    },
                )
                return
            if path.startswith("/api/session/"):
                session_id = path.removeprefix("/api/session/")
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.store.public_session(session_id),
                )
                return
            static_paths = {
                "/": STATIC_DIR / "index.html",
                "/index.html": STATIC_DIR / "index.html",
                "/app.js": STATIC_DIR / "app.js",
                "/styles.css": STATIC_DIR / "styles.css",
            }
            file_path = static_paths.get(path)
            if file_path is None or not file_path.exists():
                raise StudyError("Not found", HTTPStatus.NOT_FOUND)
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type == "application/javascript":
                content_type += "; charset=utf-8"
            self._send_bytes(HTTPStatus.OK, file_path.read_bytes(), content_type)
        except Exception as error:
            self._handle_error(error)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            body = self._read_json()
            store = self.server.application.store
            if path == "/api/session":
                payload = store.create_session(body.get("consent"), body.get("profile"))
                self._send_json(HTTPStatus.CREATED, payload)
                return
            if path == "/api/event":
                row = store.record_event(
                    body.get("session_id", ""),
                    body.get("event_type", ""),
                    body.get("task_id"),
                    body.get("elapsed_ms"),
                    body.get("payload"),
                )
                self._send_json(HTTPStatus.CREATED, {"event_id": row["event_id"]})
                return
            if path == "/api/generate":
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.generate(body),
                )
                return
            if path == "/api/task-result":
                self._send_json(HTTPStatus.CREATED, store.submit_result(body))
                return
            if path == "/api/session-complete":
                self._send_json(HTTPStatus.CREATED, store.complete_session(body))
                return
            raise StudyError("Not found", HTTPStatus.NOT_FOUND)
        except Exception as error:
            self._handle_error(error)


def print_check(task_pack: dict) -> None:
    tasks = task_pack["tasks"]
    print(f"Study: {task_pack['study']['id']} {task_pack['study']['version']}")
    print(f"Tasks: {len(tasks)}")
    print(f"Counterbalance cells: {len(DOMAIN_ORDERS) * len(CONDITIONS)}")
    for cell in range(12):
        assignment = build_assignment(tasks, cell)
        conditions = Counter(item["condition"] for item in assignment)
        domains = Counter(item["domain"] for item in assignment)
        assert conditions == {"branch": 3, "linear": 3}
        assert domains == {"programming": 2, "research": 2, "writing": 2}
    print("Assignment balance: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MSC V0.2 human study")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", default=os.getenv("MSC_STUDY_MODEL", "qwen3:4b"))
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    )
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--num-predict", type=int, default=768)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS_PATH)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    task_pack = load_task_pack(args.tasks)
    if args.check:
        print_check(task_pack)
        return
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be between 1 and 65535")
    if args.num_ctx < 1024 or args.num_predict < 1:
        raise SystemExit("num-ctx/num-predict values are invalid")
    if not 0 <= args.temperature <= 2:
        raise SystemExit("temperature must be between 0 and 2")
    settings = ModelSettings(
        model=args.model,
        base_url=args.ollama_url.rstrip("/"),
        num_ctx=args.num_ctx,
        num_predict=args.num_predict,
        temperature=args.temperature,
        timeout_seconds=args.timeout_seconds,
    )
    ollama = OllamaClient(settings)
    model_health = ollama.health()
    runtime = {
        "model": settings.model,
        "model_digest": model_health.get("model_digest"),
        "model_details": model_health.get("model_details"),
        "num_ctx": settings.num_ctx,
        "num_predict": settings.num_predict,
        "temperature": settings.temperature,
        "think": False,
    }
    application = StudyApplication(
        StudyStore(task_pack, args.data_dir, runtime=runtime),
        ollama,
    )
    server = StudyHTTPServer((args.host, args.port), StudyRequestHandler, application)
    print(f"Human study: http://{args.host}:{args.port}")
    print(f"Model: {args.model}; data: {args.data_dir.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
