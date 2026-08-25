from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from tests.test_api import FakeProvider


BACKEND_DIR = Path(__file__).resolve().parents[1]


class SummaryMergeApiTests(unittest.TestCase):
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

    def create_conversation(self) -> dict:
        response = self.client.post(
            "/api/conversations",
            json={"title": "Summary merge test", "model": "fake-model"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def chat(self, conversation_id: int, branch_id: int, content: str) -> dict:
        response = self.client.post(
            "/api/chat",
            json={
                "conversation_id": conversation_id,
                "branch_id": branch_id,
                "content": content,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_summary_citations_conflicts_preview_merge_and_rollback(self) -> None:
        conversation = self.create_conversation()
        conversation_id = conversation["id"]
        main = self.client.get(f"/api/conversations/{conversation_id}").json()["branches"][0]
        shared = self.chat(conversation_id, main["id"], "shared: baseline")
        shared_head = shared["assistant_message"]["id"]
        target = self.chat(conversation_id, main["id"], "database: postgres")

        source_response = self.client.post(
            f"/api/conversations/{conversation_id}/branches",
            json={"name": "SQLite path", "forked_from_message_id": shared_head},
        )
        self.assertEqual(source_response.status_code, 200, source_response.text)
        source = source_response.json()
        source_chat = self.chat(conversation_id, source["id"], "database: sqlite")

        target_summary_response = self.client.post(
            f"/api/branches/{main['id']}/summaries",
            json={"title": "Postgres decision"},
        )
        self.assertEqual(target_summary_response.status_code, 200, target_summary_response.text)
        target_summary = target_summary_response.json()
        self.assertTrue(target_summary["is_citable"])
        self.assertTrue(target_summary["source_message_ids"])
        self.assertIn(
            f"[m:{target['user_message']['id']}]",
            target_summary["content"],
        )
        custom_summary = self.client.post(
            f"/api/branches/{main['id']}/summaries",
            json={
                "title": "Cited claim",
                "content": "The selected database is documented here.",
                "source_message_ids": [target["user_message"]["id"]],
                "claims": [
                    {
                        "key": "database",
                        "value": "postgres",
                        "source_message_id": target["user_message"]["id"],
                    }
                ],
            },
        )
        self.assertEqual(custom_summary.status_code, 200, custom_summary.text)
        self.assertIn(
            f"[m:{target['user_message']['id']}]",
            custom_summary.json()["content"],
        )
        source_summary = self.client.post(
            f"/api/branches/{source['id']}/summaries",
            json={"title": "SQLite decision"},
        ).json()

        message_response = self.client.get(
            f"/api/messages/{target['user_message']['id']}"
        )
        self.assertEqual(message_response.status_code, 200)
        self.assertEqual(message_response.json()["content"], "database: postgres")

        preview_response = self.client.post(
            f"/api/conversations/{conversation_id}/merges/preview",
            json={
                "target_branch_id": main["id"],
                "source_branch_id": source["id"],
                "target_summary_ids": [target_summary["id"]],
                "source_summary_ids": [source_summary["id"]],
            },
        )
        self.assertEqual(preview_response.status_code, 200, preview_response.text)
        preview = preview_response.json()
        self.assertFalse(preview["can_merge"])
        self.assertEqual(preview["conflicts"][0]["subject"].casefold(), "database")

        blocked = self.client.post(
            f"/api/conversations/{conversation_id}/merges",
            json={
                "target_branch_id": main["id"],
                "source_branch_id": source["id"],
                "target_summary_ids": [target_summary["id"]],
                "source_summary_ids": [source_summary["id"]],
                "preview_token": preview["preview_token"],
            },
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertTrue(blocked.json().get("conflicts"), blocked.text)

        before_merge = self.client.get(f"/api/conversations/{conversation_id}").json()
        message_ids_before = [message["id"] for message in before_merge["messages"]]
        active_branch_before = before_merge["conversation"]["active_branch_id"]
        merged_response = self.client.post(
            f"/api/conversations/{conversation_id}/merges",
            json={
                "target_branch_id": main["id"],
                "source_branch_id": source["id"],
                "target_summary_ids": [target_summary["id"]],
                "source_summary_ids": [source_summary["id"]],
                "preview_token": preview["preview_token"],
                "resolutions": {"database": "target"},
                "name": "Resolved database merge",
            },
        )
        self.assertEqual(merged_response.status_code, 200, merged_response.text)
        merge = merged_response.json()
        self.assertEqual(merge["status"], "completed")
        self.assertEqual(merge["conflicts"][0]["status"], "resolved")
        self.assertTrue(merge["snapshot_summary"]["is_citable"])
        self.assertEqual(merge["result_branch"]["name"], "Resolved database merge")

        after_merge = self.client.get(f"/api/conversations/{conversation_id}").json()
        self.assertEqual(
            [message["id"] for message in after_merge["messages"]],
            message_ids_before,
        )
        dag = self.client.get(f"/api/conversations/{conversation_id}/dag")
        self.assertEqual(dag.status_code, 200, dag.text)
        dag_payload = dag.json()
        self.assertTrue(dag_payload["is_acyclic"])
        node_types = {node["type"] for node in dag_payload["nodes"]}
        self.assertTrue({"message", "branch", "summary", "merge"}.issubset(node_types))
        self.assertTrue(
            any(edge["relation"] == "creates" for edge in dag_payload["edges"])
        )

        activate_result = self.client.patch(
            f"/api/conversations/{conversation_id}",
            json={
                "active_branch_id": merge["result_branch"]["id"],
                "active_message_id": target["assistant_message"]["id"],
            },
        )
        self.assertEqual(activate_result.status_code, 200, activate_result.text)
        inspected = self.client.get(
            f"/api/messages/{target['assistant_message']['id']}/context"
        )
        self.assertEqual(inspected.status_code, 200, inspected.text)
        self.assertIn(
            merge["snapshot_summary"]["id"],
            inspected.json()["branch_context"]["summary_ids"],
        )
        compared = self.client.post(
            "/api/context/compare",
            json={
                "conversation_id": conversation_id,
                "branch_id": merge["result_branch"]["id"],
                "parent_id": target["assistant_message"]["id"],
                "content": "Continue from the merged evidence",
            },
        )
        self.assertEqual(compared.status_code, 200, compared.text)
        provider_messages = next(
            call["messages"]
            for call in reversed(self.provider.calls)
            if any(
                "Reversible merge snapshot" in message["content"]
                for message in call["messages"]
            )
        )
        self.assertEqual(
            sum(message["role"] == "system" for message in provider_messages),
            1,
        )
        self.assertIn("安全边界", provider_messages[0]["content"])
        self.assertTrue(
            any(
                message["role"] == "user"
                and "Reversible merge snapshot" in message["content"]
                for message in provider_messages
            )
        )

        rollback = self.client.post(
            f"/api/merges/{merge['id']}/rollback",
            json={"reason": "Keep the branch evidence separate"},
        )
        self.assertEqual(rollback.status_code, 200, rollback.text)
        self.assertEqual(rollback.json()["status"], "rolled_back")
        self.assertTrue(rollback.json()["events"][-1]["event_type"] == "rollback")
        final_state = self.client.get(f"/api/conversations/{conversation_id}").json()
        self.assertEqual(
            final_state["conversation"]["active_branch_id"],
            active_branch_before,
        )
        self.assertEqual(
            [message["id"] for message in final_state["messages"]],
            message_ids_before,
        )
        deleted_result = self.client.delete(
            f"/api/branches/{merge['result_branch']['id']}"
        )
        self.assertEqual(deleted_result.status_code, 200, deleted_result.text)
        self.assertEqual(deleted_result.json()["deleted_message_count"], 0)
        after_delete = self.client.get(f"/api/conversations/{conversation_id}").json()
        self.assertEqual(
            [message["id"] for message in after_delete["messages"]],
            message_ids_before,
        )

    def test_stale_preview_is_rejected_after_branch_changes(self) -> None:
        conversation = self.create_conversation()
        conversation_id = conversation["id"]
        main = self.client.get(f"/api/conversations/{conversation_id}").json()["branches"][0]
        shared = self.chat(conversation_id, main["id"], "shared: yes")
        source = self.client.post(
            f"/api/conversations/{conversation_id}/branches",
            json={
                "name": "Alternative",
                "forked_from_message_id": shared["assistant_message"]["id"],
            },
        ).json()
        self.chat(conversation_id, source["id"], "choice: source")
        preview = self.client.post(
            f"/api/conversations/{conversation_id}/merges/preview",
            json={"target_branch_id": main["id"], "source_branch_id": source["id"]},
        ).json()
        self.chat(conversation_id, main["id"], "new_fact: changed")
        stale = self.client.post(
            f"/api/conversations/{conversation_id}/merges",
            json={
                "target_branch_id": main["id"],
                "source_branch_id": source["id"],
                "preview_token": preview["preview_token"],
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertIn("stale", stale.json()["detail"].casefold())

    def test_merge_requires_current_summaries_from_both_branches(self) -> None:
        conversation = self.create_conversation()
        conversation_id = conversation["id"]
        main = self.client.get(f"/api/conversations/{conversation_id}").json()["branches"][0]
        shared = self.chat(conversation_id, main["id"], "shared: yes")
        source = self.client.post(
            f"/api/conversations/{conversation_id}/branches",
            json={
                "name": "Unsummarized",
                "forked_from_message_id": shared["assistant_message"]["id"],
            },
        ).json()
        self.chat(conversation_id, source["id"], "choice: source")
        preview = self.client.post(
            f"/api/conversations/{conversation_id}/merges/preview",
            json={"target_branch_id": main["id"], "source_branch_id": source["id"]},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertFalse(preview.json()["can_merge"])
        self.assertEqual(len(preview.json()["blockers"]), 2)

        blocked = self.client.post(
            f"/api/conversations/{conversation_id}/merges",
            json={
                "target_branch_id": main["id"],
                "source_branch_id": source["id"],
                "preview_token": preview.json()["preview_token"],
            },
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(len(blocked.json()["blockers"]), 2)

        source_summary = self.client.post(
            f"/api/branches/{source['id']}/summaries",
            json={},
        ).json()
        self.chat(conversation_id, source["id"], "new_fact: after summary")
        stale_summary = self.client.post(
            f"/api/conversations/{conversation_id}/merges/preview",
            json={
                "target_branch_id": main["id"],
                "source_branch_id": source["id"],
                "source_summary_ids": [source_summary["id"]],
            },
        )
        self.assertEqual(stale_summary.status_code, 400, stale_summary.text)
        self.assertIn("current branch head", stale_summary.json()["detail"])

    def test_deleting_any_cited_source_invalidates_summary(self) -> None:
        conversation = self.create_conversation()
        conversation_id = conversation["id"]
        main = self.client.get(f"/api/conversations/{conversation_id}").json()["branches"][0]
        shared = self.chat(conversation_id, main["id"], "shared: evidence")
        source = self.client.post(
            f"/api/conversations/{conversation_id}/branches",
            json={
                "name": "Disposable evidence",
                "forked_from_message_id": shared["assistant_message"]["id"],
            },
        ).json()
        source_chat = self.chat(conversation_id, source["id"], "status: candidate")
        summary = self.client.post(
            f"/api/branches/{source['id']}/summaries",
            json={
                "source_message_ids": [
                    shared["assistant_message"]["id"],
                    source_chat["user_message"]["id"],
                ]
            },
        ).json()
        deleted = self.client.delete(f"/api/branches/{source['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        loaded = self.client.get(f"/api/summaries/{summary['id']}")
        self.assertEqual(loaded.status_code, 200, loaded.text)
        self.assertEqual(loaded.json()["status"], "orphaned")
        self.assertFalse(loaded.json()["is_citable"])

    def test_summary_rejects_anchor_from_sibling_branch(self) -> None:
        conversation = self.create_conversation()
        conversation_id = conversation["id"]
        main = self.client.get(f"/api/conversations/{conversation_id}").json()["branches"][0]
        shared = self.chat(conversation_id, main["id"], "shared: evidence")
        main_only = self.chat(conversation_id, main["id"], "decision: main")
        sibling = self.client.post(
            f"/api/conversations/{conversation_id}/branches",
            json={
                "name": "Sibling",
                "forked_from_message_id": shared["assistant_message"]["id"],
            },
        ).json()
        sibling_only = self.chat(conversation_id, sibling["id"], "decision: sibling")

        invalid_anchor = self.client.post(
            f"/api/branches/{main['id']}/summaries",
            json={"anchor_message_id": sibling_only["assistant_message"]["id"]},
        )
        self.assertEqual(invalid_anchor.status_code, 400, invalid_anchor.text)
        self.assertIn("not visible", invalid_anchor.json()["detail"])

        invalid_source = self.client.post(
            f"/api/branches/{sibling['id']}/summaries",
            json={
                "source_message_ids": [main_only["user_message"]["id"]],
            },
        )
        self.assertEqual(invalid_source.status_code, 400, invalid_source.text)
        self.assertIn("must be visible", invalid_source.json()["detail"])
