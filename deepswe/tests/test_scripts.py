import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

audit_tasks = __import__("audit_tasks")
summarize_job = __import__("summarize_job")


class AuditTasksTests(unittest.TestCase):
    def make_task(self, root, name, valid=True):
        task = root / name
        (task / "tests").mkdir(parents=True)
        (task / "instruction.md").write_text("Fix it\n")
        (task / "tests/test.sh").write_text("#!/bin/sh\n")
        network_mode = "no-network" if valid else "public"
        (task / "task.toml").write_text(
            f'''schema_version = "1.3"
artifacts = ["/logs/artifacts/model.patch"]

[metadata]
task_id = "{name}"
ext_id = "ext-{name}"

[agent]
network_mode = "{network_mode}"

[verifier]
network_mode = "no-network"
environment_mode = "separate"

[[verifier.collect]]
command = "git diff > /logs/artifacts/model.patch"

[environment]
docker_image = "example/{name}:v1"
'''
        )

    def test_valid_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_task(root, "a")
            self.make_task(root, "b")
            result = audit_tasks.audit(root)
        self.assertEqual(result["task_count"], 2)
        self.assertEqual(result["invalid_tasks"], 0)

    def test_invalid_network_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_task(root, "a", valid=False)
            result = audit_tasks.audit(root)
        self.assertEqual(result["invalid_tasks"], 1)
        self.assertIn("agent.network_mode must be no-network", result["issues"]["a"])


class SummarizeJobTests(unittest.TestCase):
    def result(self, finished=True):
        return {
            "finished_at": "2026-08-10T00:00:00Z" if finished else None,
            "n_total_trials": 3,
            "stats": {
                "n_completed_trials": 3 if finished else 2,
                "n_errored_trials": 1,
                "n_running_trials": 0,
                "n_pending_trials": 0 if finished else 1,
                "n_cancelled_trials": 0,
                "n_retries": 1,
                "evals": {
                    "kimi-code__model__tasks": {
                        "reward_stats": {
                            "reward": {
                                "1.0": ["task-a__one"],
                                "0.0": ["task-b__one"],
                            }
                        },
                        "exception_stats": {"RuntimeError": ["task-c__one"]},
                    }
                },
            },
        }

    def test_errors_use_full_denominator(self):
        summary = summarize_job.summarize(self.result(), 3)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["resolved"], 1)
        self.assertEqual(summary["strict_score"], 1 / 3)
        self.assertEqual(summary["observed_reward_mean"], 1 / 2)
        self.assertEqual(summary["ungraded"], 1)

    def test_duplicate_reward_trial_is_rejected(self):
        result = self.result()
        buckets = result["stats"]["evals"]["kimi-code__model__tasks"]["reward_stats"][
            "reward"
        ]
        buckets["0.0"].append("task-a__one")
        with self.assertRaisesRegex(ValueError, "multiple reward buckets"):
            summarize_job.summarize(result, 3)

    def test_require_complete_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps(self.result(finished=False)))
            process = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "summarize_job.py"),
                    str(path),
                    "--expected",
                    "3",
                    "--require-complete",
                ],
                capture_output=True,
                text=True,
            )
        self.assertEqual(process.returncode, 2)
        self.assertIn("Status: INCOMPLETE", process.stdout)

    def test_multiple_eval_groups_require_selection(self):
        result = self.result()
        result["stats"]["evals"]["other"] = {}
        with self.assertRaisesRegex(ValueError, "multiple evaluation groups"):
            summarize_job.summarize(result, 3)


class KimiAdapterSourceTests(unittest.TestCase):
    def test_adapter_uses_real_loop_config_and_no_lossy_atif(self):
        source = (SCRIPTS / "kimi_code_agent.py").read_text()
        compile(source, "kimi_code_agent.py", "exec")
        self.assertIn("SUPPORTS_ATIF = False", source)
        self.assertIn("max_retries_per_step", source)
        self.assertIn("max_steps_per_turn", source)
        self.assertNotIn("KIMI_LOOP_MAX_STEPS_PER_TURN", source)
        self.assertIn('npm install --global --prefix "$HOME/.local"', source)
        self.assertIn("environment.upload_file", source)
        self.assertIn("install -m 600 -o agent -g agent", source)

    def test_adapter_config_file_does_not_enter_process_env(self):
        source = (SCRIPTS / "kimi_code_agent.py").read_text()
        run_source = source.split("async def run(", 1)[1]
        config_branch = run_source.split("if self.config_file:", 1)[1].split(
            "else:", 1
        )[0]
        self.assertNotIn("KIMI_MODEL_API_KEY", config_branch)


if __name__ == "__main__":
    unittest.main()
