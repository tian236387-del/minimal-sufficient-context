from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.providers import ChatResult, ChatStreamEvent, ProviderTimeoutError


BACKEND_DIR = Path(__file__).resolve().parents[1]


class FakeProvider:
    def __init__(self) -> None:
        self.calls = []
        self.stream_calls = []
        self.fail = False
        self.stream_fail_after_delta = False

    async def chat(self, model: str, messages: list[dict]) -> ChatResult:
        self.calls.append({"model": model, "messages": messages})
        if self.fail:
            raise ProviderTimeoutError("fake timeout")
        return ChatResult(
            content=f"reply-{len(self.calls)}",
            prompt_tokens=100 + len(self.calls),
            response_tokens=20,
        )

    async def stream_chat(self, model: str, messages: list[dict]):
        self.stream_calls.append({"model": model, "messages": messages})
        yield ChatStreamEvent(content="streamed ")
        if self.stream_fail_after_delta:
            raise ProviderTimeoutError("fake stream timeout")
        yield ChatStreamEvent(content=f"reply-{len(self.stream_calls)}")
        yield ChatStreamEvent(done=True, prompt_tokens=211, response_tokens=31)

    async def list_models(self) -> list[str]:
        return ["fake-model", "alternate-model"]


def parse_sse(response_text: str) -> list[tuple[str, dict]]:
    events = []
    for block in response_text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[7:]
            elif line.startswith("data: "):
                data_lines.append(line[6:])
        events.append((event_name, json.loads("\n".join(data_lines))))
    return events


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary_directory.name)
        self.provider = FakeProvider()
        settings = Settings(
            database_path=temporary_path / "api.db",
            migrations_path=BACKEND_DIR / "migrations",
            backup_path=temporary_path / "backups",
            backup_before_migrate=False,
            ollama_base_url="http://provider.invalid",
            default_model="fake-model",
            default_system_prompt="test system prompt",
        )
        self.app = create_app(settings=settings, provider=self.provider)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temporary_directory.cleanup()

    def create_conversation(self, title: str = "API test") -> dict:
        response = self.client.post(
            "/api/conversations",
            json={"title": title, "model": "fake-model"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def load_conversation(self, conversation_id: int) -> dict:
        response = self.client.get(f"/api/conversations/{conversation_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_health_models_and_new_conversation_main_branch(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ok"])
        self.assertEqual(health.json()["schema_version"], 7)

        models = self.client.get("/api/models")
        self.assertEqual(models.json()["models"], ["fake-model", "alternate-model"])
        conversation = self.create_conversation()
        loaded = self.load_conversation(conversation["id"])
        self.assertEqual(len(loaded["branches"]), 1)
        self.assertEqual(loaded["branches"][0]["name"], "Main")
        self.assertEqual(
            loaded["conversation"]["active_branch_id"],
            loaded["branches"][0]["id"],
        )

    def test_named_branch_stream_export_delete_and_active_persistence(self) -> None:
        conversation = self.create_conversation()
        loaded = self.load_conversation(conversation["id"])
        main_branch = loaded["branches"][0]
        first = self.client.post(
            "/api/chat",
            json={
                "conversation_id": conversation["id"],
                "branch_id": main_branch["id"],
                "content": "shared root",
            },
        ).json()
        fork_anchor = first["assistant_message"]["id"]

        created = self.client.post(
            f"/api/conversations/{conversation['id']}/branches",
            json={"name": "Research", "forked_from_message_id": fork_anchor},
        )
        self.assertEqual(created.status_code, 200, created.text)
        branch = created.json()
        loaded = self.load_conversation(conversation["id"])
        self.assertEqual(loaded["conversation"]["active_branch_id"], branch["id"])
        self.assertEqual(loaded["conversation"]["active_message_id"], fork_anchor)

        renamed = self.client.patch(
            f"/api/branches/{branch['id']}", json={"name": "Exploration"}
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        duplicate = self.client.post(
            f"/api/conversations/{conversation['id']}/branches",
            json={"name": "exploration", "forked_from_message_id": fork_anchor},
        )
        self.assertEqual(duplicate.status_code, 400)

        stream = self.client.post(
            "/api/chat/stream",
            json={
                "conversation_id": conversation["id"],
                "branch_id": branch["id"],
                "parent_id": fork_anchor,
                "content": "branch fact",
            },
        )
        self.assertEqual(stream.status_code, 200, stream.text)
        events = parse_sse(stream.text)
        self.assertEqual([event[0] for event in events], ["context", "delta", "delta", "done"])
        done = events[-1][1]
        self.assertEqual(done["assistant_message"]["content"], "streamed reply-1")
        self.assertEqual(done["branch"]["name"], "Exploration")

        exported_json = self.client.get(
            f"/api/branches/{branch['id']}/export?format=json"
        )
        self.assertEqual(exported_json.status_code, 200)
        self.assertEqual(exported_json.json()["branch"]["name"], "Exploration")
        self.assertEqual(len(exported_json.json()["messages"]), 4)
        exported_markdown = self.client.get(
            f"/api/branches/{branch['id']}/export?format=markdown"
        )
        self.assertIn("# API test", exported_markdown.text)
        self.assertIn("### Assistant", exported_markdown.text)

        deleted = self.client.delete(f"/api/branches/{branch['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["deleted_message_count"], 2)
        loaded = self.load_conversation(conversation["id"])
        self.assertEqual(len(loaded["messages"]), 2)
        self.assertEqual(loaded["conversation"]["active_branch_id"], main_branch["id"])
        protected = self.client.delete(f"/api/branches/{main_branch['id']}")
        self.assertEqual(protected.status_code, 409)

    def test_active_message_selection_is_persisted_and_validated(self) -> None:
        conversation = self.create_conversation()
        main_branch = self.load_conversation(conversation["id"])["branches"][0]
        first = self.client.post(
            "/api/chat",
            json={"conversation_id": conversation["id"], "content": "hello"},
        ).json()
        selected_id = first["user_message"]["id"]
        updated = self.client.patch(
            f"/api/conversations/{conversation['id']}",
            json={
                "active_branch_id": main_branch["id"],
                "active_message_id": selected_id,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        reloaded = self.load_conversation(conversation["id"])
        self.assertEqual(reloaded["conversation"]["active_message_id"], selected_id)

        second = self.create_conversation("Second")
        invalid = self.client.patch(
            f"/api/conversations/{second['id']}",
            json={
                "active_branch_id": main_branch["id"],
                "active_message_id": selected_id,
            },
        )
        self.assertEqual(invalid.status_code, 400)

    def test_linear_branch_ab_returns_context_diff_without_persisting(self) -> None:
        conversation = self.create_conversation()
        main_branch = self.load_conversation(conversation["id"])["branches"][0]
        shared = self.client.post(
            "/api/chat",
            json={"conversation_id": conversation["id"], "content": "shared"},
        ).json()
        shared_head = shared["assistant_message"]["id"]
        branch_a = self.client.post(
            "/api/chat",
            json={
                "conversation_id": conversation["id"],
                "branch_id": main_branch["id"],
                "parent_id": shared_head,
                "content": "linear-only fact",
            },
        ).json()
        branch_b = self.client.post(
            f"/api/conversations/{conversation['id']}/branches",
            json={"name": "Alternative", "forked_from_message_id": shared_head},
        ).json()
        branch_b_chat = self.client.post(
            "/api/chat",
            json={
                "conversation_id": conversation["id"],
                "branch_id": branch_b["id"],
                "parent_id": shared_head,
                "content": "branch-only fact",
            },
        ).json()
        message_count_before = len(self.load_conversation(conversation["id"])["messages"])

        compared = self.client.post(
            "/api/context/compare",
            json={
                "conversation_id": conversation["id"],
                "branch_id": branch_b["id"],
                "parent_id": branch_b_chat["assistant_message"]["id"],
                "content": "Which fact is active?",
            },
        )
        self.assertEqual(compared.status_code, 200, compared.text)
        result = compared.json()
        self.assertFalse(result["persisted"])
        self.assertIn(
            branch_a["user_message"]["id"],
            result["context_diff"]["linear_only_message_ids"],
        )
        self.assertNotIn(
            branch_a["user_message"]["id"],
            result["branch"]["context"]["included_message_ids"],
        )
        self.assertEqual(
            len(self.load_conversation(conversation["id"])["messages"]),
            message_count_before,
        )

    def test_context_inspector_explains_branch_sources_and_exclusions(self) -> None:
        conversation = self.create_conversation()
        main = self.load_conversation(conversation["id"])["branches"][0]
        shared = self.client.post(
            "/api/chat",
            json={"conversation_id": conversation["id"], "content": "shared context"},
        ).json()
        self.client.post(
            "/api/chat",
            json={
                "conversation_id": conversation["id"],
                "branch_id": main["id"],
                "parent_id": shared["assistant_message"]["id"],
                "content": "main-only context",
            },
        )
        branch = self.client.post(
            f"/api/conversations/{conversation['id']}/branches",
            json={
                "name": "Focused path",
                "forked_from_message_id": shared["assistant_message"]["id"],
            },
        ).json()
        branch_reply = self.client.post(
            "/api/chat",
            json={
                "conversation_id": conversation["id"],
                "branch_id": branch["id"],
                "parent_id": shared["assistant_message"]["id"],
                "content": "branch-only context",
            },
        ).json()

        inspected = self.client.get(
            f"/api/messages/{branch_reply['assistant_message']['id']}/context"
        )
        self.assertEqual(inspected.status_code, 200, inspected.text)
        payload = inspected.json()
        self.assertEqual(payload["selected_message_id"], branch_reply["assistant_message"]["id"])
        self.assertEqual(payload["active_branch_name"], "Focused path")
        self.assertEqual(payload["context_explanation"]["strategy"], "branch")
        self.assertGreaterEqual(payload["excluded_message_count"], 2)
        self.assertEqual(
            payload["last_prompt_tokens"],
            branch_reply["assistant_message"]["prompt_tokens"],
        )
        included_ids = {message["id"] for message in payload["active_path"]}
        excluded_ids = {message["id"] for message in payload["excluded_siblings"]}
        self.assertIn(branch_reply["user_message"]["id"], included_ids)
        self.assertNotIn(branch_reply["user_message"]["id"], excluded_ids)

    def test_token_budget_update_prunes_and_rejects_oversized_prompt(self) -> None:
        conversation = self.create_conversation()
        for index in range(4):
            loaded = self.load_conversation(conversation["id"])
            branch = next(item for item in loaded["branches"] if item["is_main"])
            response = self.client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "branch_id": branch["id"],
                    "content": f"turn {index} " + ("x" * 500),
                },
            )
            self.assertEqual(response.status_code, 200, response.text)

        updated = self.client.patch(
            f"/api/conversations/{conversation['id']}",
            json={"token_budget": 256},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        loaded = updated.json()
        branch = next(item for item in loaded["branches"] if item["is_main"])
        calls_before = len(self.provider.calls)
        response = self.client.post(
            "/api/chat",
            json={
                "conversation_id": conversation["id"],
                "branch_id": branch["id"],
                "content": "continue",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        context = response.json()["context"]
        self.assertLessEqual(context["estimated_tokens"], 256)
        self.assertTrue(context["truncated_message_ids"])
        self.assertEqual(len(self.provider.calls), calls_before + 1)

        oversized = self.client.post(
            "/api/chat",
            json={
                "conversation_id": conversation["id"],
                "branch_id": branch["id"],
                "content": "z" * 2000,
            },
        )
        self.assertEqual(oversized.status_code, 422)
        self.assertEqual(len(self.provider.calls), calls_before + 1)

    def test_stream_failure_does_not_persist_partial_exchange(self) -> None:
        conversation = self.create_conversation()
        branch = self.load_conversation(conversation["id"])["branches"][0]
        self.provider.stream_fail_after_delta = True
        response = self.client.post(
            "/api/chat/stream",
            json={
                "conversation_id": conversation["id"],
                "branch_id": branch["id"],
                "content": "will fail",
            },
        )
        events = parse_sse(response.text)
        self.assertEqual(events[-1][0], "error")
        self.assertEqual(events[-1][1]["status_code"], 504)
        self.assertEqual(self.load_conversation(conversation["id"])["messages"], [])

    def test_provider_failure_and_cross_conversation_parent_are_rejected(self) -> None:
        first_conversation = self.create_conversation("First")
        second_conversation = self.create_conversation("Second")
        first_chat = self.client.post(
            "/api/chat",
            json={"conversation_id": first_conversation["id"], "content": "root"},
        ).json()
        calls_before = len(self.provider.calls)
        invalid = self.client.post(
            "/api/chat",
            json={
                "conversation_id": second_conversation["id"],
                "parent_id": first_chat["assistant_message"]["id"],
                "content": "invalid",
            },
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(len(self.provider.calls), calls_before)

        self.provider.fail = True
        failed = self.client.post(
            "/api/chat",
            json={"conversation_id": second_conversation["id"], "content": "fail"},
        )
        self.assertEqual(failed.status_code, 504)
        self.assertEqual(self.load_conversation(second_conversation["id"])["messages"], [])
