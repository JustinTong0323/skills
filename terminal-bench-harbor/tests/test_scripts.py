from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

harbor_results = __import__("harbor_results")
config_differences = harbor_results.config_differences
is_complete = harbor_results.is_complete
normalized_config = harbor_results.normalized_config
summarize = harbor_results.summarize
summarize_eval = harbor_results.summarize_eval
task_outcomes = harbor_results.task_outcomes
compare_jobs = __import__("compare_jobs")


class HarborResultsTests(unittest.TestCase):
    def result(self, finished: bool = True) -> dict:
        rewards = {
            "1.0": ["task-a__one"],
            "0.0": ["task-a__two", "task-b__one", "task-b__two"],
        }
        if not finished:
            rewards["0.0"].pop()
        return {
            "id": "job-id",
            "started_at": "2026-08-01T00:00:00",
            "finished_at": "2026-08-01T01:00:00" if finished else None,
            "n_total_trials": 4,
            "stats": {
                "n_completed_trials": 4 if finished else 3,
                "n_errored_trials": 0,
                "n_running_trials": 0,
                "n_pending_trials": 0 if finished else 1,
                "n_cancelled_trials": 0,
                "n_retries": 0,
                "evals": {
                    "agent__model__dataset": {
                        "n_trials": 4 if finished else 3,
                        "n_errors": 0,
                        "metrics": [{"mean": 0.25}],
                        "pass_at_k": {},
                        "reward_stats": {"reward": rewards},
                    }
                },
            },
        }

    def test_task_outcomes_group_attempts(self) -> None:
        eval_result = next(iter(self.result()["stats"]["evals"].values()))
        outcomes = task_outcomes(eval_result)
        self.assertTrue(outcomes["task-a"]["passed"])
        self.assertEqual(outcomes["task-a"]["attempts"], 2)
        self.assertFalse(outcomes["task-b"]["passed"])

    def test_fractional_reward_is_not_a_pass(self) -> None:
        eval_result = {
            "reward_stats": {
                "reward": {
                    "1.0": ["task-a__one"],
                    "0.5": ["task-b__one"],
                }
            }
        }
        outcomes = task_outcomes(eval_result)
        summary = summarize_eval("eval", eval_result, 2, 1, True, None)
        self.assertTrue(outcomes["task-a"]["passed"])
        self.assertFalse(outcomes["task-b"]["passed"])
        self.assertEqual(summary["passed_trials"], 1)
        self.assertEqual(summary["failed_trials"], 1)
        self.assertEqual(summary["pass_at_attempts"], 0.5)

    def test_complete_summary_reports_pass_at_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(self.result()))
            (job / "config.json").write_text(
                json.dumps({"job_name": "test", "n_attempts": 2})
            )
            value = summarize(job)
        self.assertTrue(value["complete"])
        self.assertEqual(value["evals"][0]["pass_at_attempts"], 0.5)

    def test_incomplete_summary_is_not_final(self) -> None:
        result = self.result(finished=False)
        self.assertFalse(is_complete(result))
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(result))
            (job / "config.json").write_text(
                json.dumps({"job_name": "test", "n_attempts": 2})
            )
            value = summarize(job)
        self.assertIsNone(value["evals"][0]["pass_at_attempts"])
        self.assertEqual(value["evals"][0]["partial_pass_at_attempts_lower_bound"], 0.5)
        self.assertEqual(value["evals"][0]["partial_pass_at_attempts_upper_bound"], 1.0)

    def test_pass_at_one_target_becomes_unreachable(self) -> None:
        result = self.result(finished=False)
        result["stats"]["evals"]["agent__model__dataset"]["reward_stats"] = {
            "reward": {
                "1.0": ["task-a__one"],
                "0.0": ["task-b__one", "task-c__one"],
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(result))
            (job / "config.json").write_text(
                json.dumps({"job_name": "test", "n_attempts": 1})
            )
            value = summarize(job, target_passes=3)
            tie_value = summarize(job, target_passes=2)
        eval_summary = value["evals"][0]
        self.assertEqual(eval_summary["optimistic_successful_tasks"], 2)
        self.assertFalse(eval_summary["target_reachable"])
        self.assertTrue(tie_value["evals"][0]["target_reachable"])

    def test_cancelled_completed_slot_is_not_a_graded_outcome(self) -> None:
        result = self.result()
        result["stats"]["n_cancelled_trials"] = 1
        result["stats"]["n_errored_trials"] = 1
        result["stats"]["evals"]["agent__model__dataset"]["n_errors"] = 1
        result["stats"]["evals"]["agent__model__dataset"]["reward_stats"] = {
            "reward": {
                "1.0": ["task-a__one"],
                "0.0": ["task-b__one", "task-c__one"],
            }
        }
        result["stats"]["evals"]["agent__model__dataset"]["exception_stats"] = {
            "CancelledError": ["task-d__one"]
        }
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(result))
            (job / "config.json").write_text(
                json.dumps({"job_name": "test", "n_attempts": 1})
            )
            value = summarize(job, target_passes=3)
        eval_summary = value["evals"][0]
        self.assertFalse(value["complete"])
        self.assertEqual(value["n_completed_trials"], 4)
        self.assertEqual(eval_summary["graded_trials"], 3)
        self.assertEqual(eval_summary["ungraded_trials"], 1)
        self.assertEqual(eval_summary["optimistic_successful_tasks"], 2)
        self.assertFalse(eval_summary["target_reachable"])

    def test_terminal_error_consumes_an_attempt(self) -> None:
        value = summarize_eval(
            "eval",
            {
                "reward_stats": {"reward": {"1.0": ["task-a__one"]}},
                "exception_stats": {"RuntimeError": ["task-b__one"]},
            },
            expected_tasks=2,
            attempts=1,
            complete=False,
            target_passes=2,
        )
        self.assertEqual(value["exhausted_failed_tasks"], 1)
        self.assertEqual(value["optimistic_successful_tasks"], 1)
        self.assertFalse(value["target_reachable"])

    def test_met_target_is_reachable_without_expected_task_count(self) -> None:
        value = summarize_eval(
            "eval",
            {"reward_stats": {"reward": {"1.0": ["task-a__one"]}}},
            expected_tasks=None,
            attempts=1,
            complete=False,
            target_passes=1,
        )
        self.assertTrue(value["target_met"])
        self.assertTrue(value["target_reachable"])

    def test_config_comparison_ignores_only_selected_top_level_keys(self) -> None:
        left = {"job_name": "left", "agents": [{"kwargs": {"top_p": 0.95}}]}
        right = {"job_name": "right", "agents": [{"kwargs": {"top_p": 1.0}}]}
        left = normalized_config(left)
        right = normalized_config(right)
        self.assertEqual(config_differences(left, right), ["$.agents[0].kwargs.top_p"])

    def test_target_cli_uses_distinct_unreachable_exit(self) -> None:
        result = self.result(finished=False)
        result["stats"]["evals"]["agent__model__dataset"]["reward_stats"] = {
            "reward": {
                "1.0": ["task-a__one"],
                "0.0": ["task-b__one", "task-c__one"],
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(result))
            (job / "config.json").write_text(
                json.dumps({"job_name": "test", "n_attempts": 1})
            )
            process = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "summarize_job.py"),
                    str(job),
                    "--target-passes",
                    "3",
                    "--fail-if-target-unreachable",
                ],
                capture_output=True,
                text=True,
            )
        self.assertEqual(process.returncode, 3)
        self.assertIn("optimistic ceiling: 2", process.stdout)

    def test_compare_configs_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left.json"
            right = root / "right.json"
            left.write_text(json.dumps({"job_name": "left", "value": 1}))
            right.write_text(json.dumps({"job_name": "right", "value": 1}))
            process = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "compare_configs.py"),
                    str(left),
                    str(right),
                ],
                capture_output=True,
                text=True,
            )
        self.assertEqual(process.returncode, 0)
        self.assertIn("Equivalent: True", process.stdout)

    def test_compare_jobs_reports_missing_configs_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left"
            right = root / "right"
            left.mkdir()
            right.mkdir()
            (left / "result.json").write_text(json.dumps(self.result()))
            (right / "result.json").write_text(json.dumps(self.result()))
            value = compare_jobs.compare(
                str(left), str(right), None, None, allow_partial=False
            )
        self.assertEqual(value["config"]["available"], {"left": False, "right": False})
        self.assertIsNone(value["config"]["equivalent"])


class RenderConfigTests(unittest.TestCase):
    def run_renderer(self, agent: str, directory: Path) -> tuple[dict, dict | None]:
        output = directory / "job.json"
        models = directory / "models.json"
        command = [
            sys.executable,
            str(SCRIPTS / "render_config.py"),
            "--agent",
            agent,
            "--job-name",
            "job",
            "--jobs-dir",
            str(directory / "jobs"),
            "--model",
            "org/model",
            "--api-base",
            "http://server:30000/v1",
            "--dataset-ref",
            "sha256:digest",
            "--context-window",
            "100000",
            "--max-output-tokens",
            "32000",
            "--output",
            str(output),
        ]
        if agent == "pi":
            command.extend(("--pi-models-path", str(models)))
        subprocess.run(command, check=True)
        job = json.loads(output.read_text())
        model_config = json.loads(models.read_text()) if models.exists() else None
        return job, model_config

    def test_terminus_uses_openai_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job, _ = self.run_renderer("terminus-2", Path(directory))
        self.assertEqual(
            job["agents"][0]["kwargs"]["api_base"], "http://server:30000/v1"
        )
        self.assertEqual(job["agents"][0]["model_name"], "openai/org/model")
        self.assertEqual(job["agents"][0]["env"]["OPENAI_API_KEY"], "EMPTY")

    def test_claude_uses_server_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job, _ = self.run_renderer("claude-code", Path(directory))
        self.assertEqual(
            job["agents"][0]["env"]["ANTHROPIC_BASE_URL"], "http://server:30000"
        )
        self.assertEqual(job["agents"][0]["env"]["CLAUDE_CODE_ATTRIBUTION_HEADER"], "0")

    def test_pi_writes_models_and_mount(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job, models = self.run_renderer("pi", Path(directory))
        self.assertIsNotNone(models)
        self.assertEqual(models["providers"]["sglang"]["api"], "openai-completions")
        self.assertEqual(
            job["environment"]["mounts"][0]["target"], "/root/.pi/agent/models.json"
        )


if __name__ == "__main__":
    unittest.main()
