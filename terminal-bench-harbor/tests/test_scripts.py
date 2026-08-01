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
is_complete = harbor_results.is_complete
summarize = harbor_results.summarize
task_outcomes = harbor_results.task_outcomes


class HarborResultsTests(unittest.TestCase):
    def result(self, finished: bool = True) -> dict:
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
                        "reward_stats": {
                            "reward": {
                                "1.0": ["task-a__one"],
                                "0.0": ["task-a__two", "task-b__one", "task-b__two"],
                            }
                        },
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

    def test_claude_uses_server_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job, _ = self.run_renderer("claude-code", Path(directory))
        self.assertEqual(
            job["agents"][0]["env"]["ANTHROPIC_BASE_URL"], "http://server:30000"
        )

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
