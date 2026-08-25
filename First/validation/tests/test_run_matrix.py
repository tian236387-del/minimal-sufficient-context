from __future__ import annotations

import sys
import csv
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path


VALIDATION_DIR = Path(__file__).resolve().parents[1]
if str(VALIDATION_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATION_DIR))

import run_matrix as matrix


class MatrixConfigTests(unittest.TestCase):
    def test_expected_job_counts(self) -> None:
        smoke = matrix.load_config(VALIDATION_DIR / "matrix_smoke.json")
        pilot = matrix.load_config(VALIDATION_DIR / "matrix_pilot.json")
        repeated = matrix.load_config(
            VALIDATION_DIR / "matrix_repeated_local.json"
        )
        cross_model_repeated = matrix.load_config(
            VALIDATION_DIR / "matrix_cross_model_repeated.json"
        )

        self.assertEqual(matrix.expected_job_count(smoke), 32)
        self.assertEqual(matrix.expected_job_count(pilot), 11_520)
        self.assertEqual(matrix.expected_job_count(repeated), 64)
        self.assertEqual(matrix.expected_job_count(cross_model_repeated), 256)

    def test_long_history_generation_honors_configured_range(self) -> None:
        config = matrix.load_config(VALIDATION_DIR / "matrix_smoke.json")
        data = matrix.generate_benchmark(config, dataset_seed=42)
        lengths = [
            len(branch["history"])
            for family in data["families"].values()
            for branch in family["branches"].values()
        ]

        self.assertGreaterEqual(min(lengths), config.history_min)
        self.assertLessEqual(max(lengths), config.history_max)

    def test_stratified_cases_span_all_families(self) -> None:
        cases = [
            {"id": f"case-{index}", "family_id": f"family-{index // 10}"}
            for index in range(100)
        ]

        selected = matrix.stratified_cases(cases, 20)
        family_counts = Counter(case["family_id"] for case in selected)

        self.assertEqual(len(selected), 20)
        self.assertEqual(len({case["id"] for case in selected}), 20)
        self.assertEqual(selected[0]["id"], "case-0")
        self.assertEqual(selected[-1]["id"], "case-99")
        self.assertEqual(set(family_counts.values()), {2})


class RunIdentityTests(unittest.TestCase):
    def make_job(self) -> matrix.MatrixJob:
        return matrix.MatrixJob(
            model=matrix.ModelSpec(name="qwen3:4b", family="Qwen", size="4B"),
            dataset_seed=42,
            inference_seed=11,
            repeat=1,
            num_ctx=4096,
            case={"id": "nova_q01"},
            condition="branch",
            messages=[],
            history_min=230,
            history_max=270,
            temperature=0.2,
            think=False,
            num_predict=256,
        )

    def test_run_id_is_stable(self) -> None:
        job = self.make_job()

        self.assertEqual(job.run_id, self.make_job().run_id)
        self.assertTrue(job.run_id.startswith("run-"))

    def test_run_id_covers_inference_settings(self) -> None:
        job = self.make_job()

        self.assertNotEqual(job.run_id, replace(job, inference_seed=29).run_id)
        self.assertNotEqual(job.run_id, replace(job, num_ctx=8192).run_id)
        self.assertNotEqual(job.run_id, replace(job, temperature=0.3).run_id)
        self.assertNotEqual(job.run_id, replace(job, think=True).run_id)
        self.assertNotEqual(job.run_id, replace(job, num_predict=128).run_id)


class SummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = matrix.load_config(VALIDATION_DIR / "matrix_smoke.json")

    @staticmethod
    def make_row(
        run_id: str,
        condition: str,
        *,
        status: str = "ok",
        correct: bool = False,
        polluted: bool = False,
        prompt_tokens: int | None = 100,
    ) -> dict:
        return {
            "run_id": run_id,
            "status": status,
            "error": "failed" if status == "error" else "",
            "model": "qwen3:4b",
            "dataset_seed": 42,
            "inference_seed": 11,
            "repeat": 1,
            "num_ctx": 4096,
            "case_id": "nova_q01",
            "condition": condition,
            "correct": correct,
            "polluted": polluted,
            "prompt_tokens": prompt_tokens,
            "latency_ms": 1000.0,
            "tokens_per_second": 20.0,
            "context_saturation": 0.25,
        }

    def test_wilson_intervals(self) -> None:
        self.assertIsNone(matrix.wilson_interval(0, 0))
        self.assertEqual(matrix.wilson_interval(0, 10), [0, 0.2775])
        self.assertEqual(matrix.wilson_interval(10, 10), [0.7225, 1])
        self.assertEqual(matrix.wilson_interval(50, 100), [0.4038, 0.5962])

    def test_summary_uses_latest_attempt_and_pairs_conditions(self) -> None:
        rows = [
            self.make_row("linear", "linear_tagged", status="error"),
            self.make_row(
                "linear",
                "linear_tagged",
                correct=False,
                polluted=True,
                prompt_tokens=100,
            ),
            self.make_row(
                "branch",
                "branch",
                correct=True,
                polluted=False,
                prompt_tokens=40,
            ),
            self.make_row("unpaired-error", "branch", status="error"),
        ]

        summary = matrix.build_summary(rows, self.config)
        paired = summary["paired_branch_vs_linear_tagged"]

        self.assertEqual(summary["expected_jobs"], 32)
        self.assertEqual(summary["observed_jobs"], 3)
        self.assertEqual(summary["pending_jobs"], 29)
        self.assertEqual(summary["completion_rate"], 0.0938)
        self.assertEqual(summary["attempt_rows"], 4)
        self.assertEqual(summary["rows"], 3)
        self.assertEqual(summary["successful_rows"], 2)
        self.assertEqual(summary["error_rows"], 1)
        self.assertEqual(paired["pairs"], 1)
        self.assertEqual(paired["avg_accuracy_delta"], 1.0)
        self.assertEqual(paired["avg_pollution_delta"], -1.0)
        self.assertEqual(paired["avg_prompt_token_reduction_pct"], 60.0)
        self.assertEqual(summary["errors"][0]["run_id"], "unpaired-error")

    def test_repeatability_summary_reports_agreement(self) -> None:
        stable_one = self.make_row("stable-1", "branch", correct=True)
        stable_two = self.make_row("stable-2", "branch", correct=True)
        stable_one.update({"repeat": 1, "answer": "same"})
        stable_two.update({"repeat": 2, "answer": "same"})
        varied_one = self.make_row("varied-1", "linear_tagged", correct=False)
        varied_two = self.make_row("varied-2", "linear_tagged", correct=True)
        varied_one.update({"repeat": 1, "answer": "first"})
        varied_two.update({"repeat": 2, "answer": "second"})

        summary = matrix.summarize_repeatability(
            [stable_one, stable_two, varied_one, varied_two]
        )

        self.assertEqual(summary["groups"], 2)
        self.assertEqual(summary["accuracy_agreement_rate"], 0.5)
        self.assertEqual(summary["pollution_agreement_rate"], 1.0)
        self.assertEqual(summary["exact_answer_agreement_rate"], 0.5)
        self.assertEqual(summary["avg_unique_answers"], 1.5)

    def test_csv_append_preserves_existing_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            path.write_text("run_id,status\nold,ok\n", encoding="utf-8-sig")

            matrix.append_csv(
                path,
                {"run_id": "new", "status": "ok", "model_digest": "digest"},
            )

            with path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows, [
                {"run_id": "old", "status": "ok"},
                {"run_id": "new", "status": "ok"},
            ])

    def test_schema_migration_adds_digest_and_preserves_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            path.write_text(
                "run_id,model,status\nold,qwen3:1.7b,ok\n",
                encoding="utf-8-sig",
            )

            migrated = matrix.ensure_csv_schema(
                path,
                {"qwen3:1.7b": "digest-1"},
            )

            self.assertTrue(migrated)
            self.assertTrue(path.with_name("results.csv.pre_schema_migration").exists())
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertIn("model_digest", rows[0])
            self.assertEqual(rows[0]["model_digest"], "digest-1")


if __name__ == "__main__":
    unittest.main()
