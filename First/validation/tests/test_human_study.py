from __future__ import annotations

import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path


HUMAN_STUDY_DIR = Path(__file__).resolve().parents[1] / "human_study"
if str(HUMAN_STUDY_DIR) not in sys.path:
    sys.path.insert(0, str(HUMAN_STUDY_DIR))

import study_server as study


class AssignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.task_pack = study.load_task_pack()

    def test_all_cells_balance_conditions_and_domains(self) -> None:
        task_condition_counts = Counter()
        for cell in range(12):
            assignment = study.build_assignment(self.task_pack["tasks"], cell)
            self.assertEqual(Counter(item["condition"] for item in assignment), {"branch": 3, "linear": 3})
            self.assertEqual(
                Counter(item["domain"] for item in assignment),
                {"programming": 2, "research": 2, "writing": 2},
            )
            for item in assignment:
                task_condition_counts[(item["task_id"], item["condition"])] += 1

        self.assertEqual(set(task_condition_counts.values()), {6})

    def test_each_domain_crosses_conditions_within_session(self) -> None:
        assignment = study.build_assignment(self.task_pack["tasks"], cell=7)
        for domain in ("programming", "research", "writing"):
            conditions = {
                item["condition"] for item in assignment if item["domain"] == domain
            }
            self.assertEqual(conditions, {"branch", "linear"})


class ContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        task_pack = study.load_task_pack()
        cls.task = next(task for task in task_pack["tasks"] if task["id"] == "programming-a")

    def test_branch_context_excludes_sibling_facts(self) -> None:
        branch = study.compile_messages(self.task, "branch", "给出修复")
        linear = study.compile_messages(self.task, "linear", "给出修复")
        branch_text = "\n".join(message["content"] for message in branch)
        linear_text = "\n".join(message["content"] for message in linear)

        self.assertIn("webhook_receipts", branch_text)
        self.assertNotIn("DynamoDB", branch_text)
        self.assertIn("DynamoDB", linear_text)
        self.assertLess(len(branch), len(linear))
        self.assertLess(study.estimate_tokens(branch), study.estimate_tokens(linear))

    def test_prior_turns_precede_latest_prompt(self) -> None:
        messages = study.compile_messages(
            self.task,
            "branch",
            "第二次请求",
            [
                {"role": "user", "content": "第一次请求"},
                {"role": "assistant", "content": "第一次回答"},
            ],
        )

        self.assertEqual(messages[-3]["content"], "第一次请求")
        self.assertEqual(messages[-2]["content"], "第一次回答")
        self.assertIn("第二次请求", messages[-1]["content"])

    def test_term_detection_normalizes_width_spaces_and_commas(self) -> None:
        answer = "覆盖 １２００ 名居民，周期 18个月。"

        self.assertEqual(study.detect_terms(answer, ["1,200", "18 个月"]), ["1,200", "18 个月"])


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = study.StudyStore(
            study.load_task_pack(),
            Path(self.temp_dir.name),
        )
        self.profile = {
            "experience": "intermediate",
            "primary_domain": "mixed",
            "ai_frequency": "weekly",
        }

    def test_session_allocation_fills_every_cell_once(self) -> None:
        sessions = [
            self.store.create_session(True, self.profile)
            for _ in range(12)
        ]

        self.assertEqual(
            {session["counterbalance_cell"] for session in sessions},
            set(range(12)),
        )
        persisted = study.StudyStore._read_jsonl(self.store.sessions_path)
        self.assertEqual(
            {row["task_pack_sha256"] for row in persisted},
            {self.store.task_pack_sha256},
        )

    def test_branch_public_task_hides_siblings(self) -> None:
        session = self.store.create_session(True, self.profile)
        branch_assignment = next(
            item for item in session["assignment"] if item["condition"] == "branch"
        )

        self.assertEqual(len(branch_assignment["task"]["branches"]), 1)
        self.assertEqual(
            branch_assignment["task"]["branches"][0]["id"],
            branch_assignment["task"]["active_branch_id"],
        )

    def test_result_requires_an_ai_draft(self) -> None:
        session = self.store.create_session(True, self.profile)
        assignment = session["assignment"][0]
        body = {
            "session_id": session["session_id"],
            "task_id": assignment["task_id"],
            "final_answer": "这是一段明确超过四十个字符的实验答案，用于确认没有生成草稿时不能提交正式任务结果，并验证校验顺序保持稳定。",
            "ratings": {
                "confidence": 4,
                "workload": 4,
                "usability": 4,
                "trust": 4,
            },
            "elapsed_ms": 60_000,
            "active_ms": 50_000,
        }

        with self.assertRaisesRegex(study.StudyError, "至少一份 AI 草稿"):
            self.store.submit_result(body)


if __name__ == "__main__":
    unittest.main()
