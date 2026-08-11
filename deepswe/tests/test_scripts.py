import ast
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

audit_tasks = __import__("audit_tasks")
kimi_config = __import__("kimi_config")
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

    def recovery_result(self, task="task-c", reward=1.0):
        return {
            "finished_at": "2026-08-11T00:00:00Z",
            "n_total_trials": 1,
            "stats": {
                "n_completed_trials": 1,
                "n_errored_trials": 0,
                "n_running_trials": 0,
                "n_pending_trials": 0,
                "n_cancelled_trials": 0,
                "n_retries": 0,
                "evals": {
                    "kimi-code__model__tasks": {
                        "reward_stats": {"reward": {str(reward): [f"{task}__retry"]}},
                        "exception_stats": {},
                    }
                },
            },
        }

    def test_recovery_overlay_preserves_original_and_corrected_scores(self):
        summary = summarize_job.overlay_recovery(
            self.result(),
            self.recovery_result(),
            3,
            ["task-c"],
            ["RuntimeError"],
        )
        self.assertEqual(summary["original"]["strict_score"], 1 / 3)
        self.assertEqual(summary["recovery"]["strict_score"], 1.0)
        self.assertEqual(summary["corrected"]["strict_score"], 2 / 3)
        self.assertEqual(summary["corrected"]["ungraded"], 0)

    def test_recovery_overlay_rejects_graded_task(self):
        with self.assertRaisesRegex(ValueError, "not original errors"):
            summarize_job.overlay_recovery(
                self.result(),
                self.recovery_result("task-a"),
                3,
                ["task-a"],
                ["RuntimeError"],
            )

    def test_recovery_overlay_rejects_disallowed_error_type(self):
        result = self.result()
        eval_result = result["stats"]["evals"]["kimi-code__model__tasks"]
        eval_result["exception_stats"] = {"VerifierTimeoutError": ["task-c__one"]}
        with self.assertRaisesRegex(ValueError, "disallowed error types"):
            summarize_job.overlay_recovery(
                result,
                self.recovery_result(),
                3,
                ["task-c"],
                ["RuntimeError"],
            )

    def test_recovery_overlay_rejects_mismatched_task(self):
        with self.assertRaisesRegex(ValueError, "recovery task mismatch"):
            summarize_job.overlay_recovery(
                self.result(),
                self.recovery_result("task-other"),
                3,
                ["task-c"],
                ["RuntimeError"],
            )

    def test_recovery_overlay_rejects_inconsistent_error_stats(self):
        recovery = self.recovery_result()
        recovery["stats"]["n_errored_trials"] = 1
        with self.assertRaisesRegex(ValueError, "contains errored trials"):
            summarize_job.overlay_recovery(
                self.result(),
                recovery,
                3,
                ["task-c"],
                ["RuntimeError"],
            )


class KimiAdapterSourceTests(unittest.TestCase):
    def path_prefix(self):
        source = (SCRIPTS / "kimi_code_agent.py").read_text()
        tree = ast.parse(source)
        kimi_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "KimiCode"
        )
        function = next(
            node
            for node in kimi_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "_path_prefix"
        )
        return ast.literal_eval(function.body[0].value)

    def test_adapter_uses_real_loop_config_and_no_lossy_atif(self):
        source = (SCRIPTS / "kimi_code_agent.py").read_text()
        compile(source, "kimi_code_agent.py", "exec")
        self.assertIn("SUPPORTS_ATIF = False", source)
        self.assertIn("max_retries_per_step", source)
        self.assertIn("max_steps_per_turn", source)
        self.assertNotIn("KIMI_LOOP_MAX_STEPS_PER_TURN", source)
        self.assertIn('npm install --global --prefix "$HOME/.local"', source)
        self.assertEqual(source.count("|| command -v nvm >/dev/null;"), 2)
        self.assertIn("environment.upload_file", source)
        self.assertIn('"$(id -u)" "$(id -g)" "$HOME"', source)
        self.assertIn("install -m 600 -o {uid} -g {gid}", source)
        self.assertIn('"CHOKIDAR_USEPOLLING": "true"', source)
        self.assertIn("watch_poll_interval_ms", source)

    def test_adapter_uploads_config_without_interpolating_contents(self):
        source = (SCRIPTS / "kimi_code_agent.py").read_text()
        run_source = source.split("async def run(", 1)[1]
        self.assertIn(
            "await environment.upload_file(config_file, remote_config)", run_source
        )
        self.assertNotIn("config_file.read_text()", run_source)

    def test_nvm_source_guard_accepts_loaded_function_only(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            nvm_dir = home / ".nvm"
            nvm_dir.mkdir()
            nvm_script = nvm_dir / "nvm.sh"
            nvm_script.write_text("nvm() { :; }\nreturn 3\n")
            env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
            loaded = subprocess.run(
                [
                    "bash",
                    "-c",
                    f"set -euo pipefail; {self.path_prefix()}command -v nvm",
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            nvm_script.write_text("return 3\n")
            missing = subprocess.run(
                ["bash", "-c", f"set -euo pipefail; {self.path_prefix()}true"],
                env=env,
                capture_output=True,
                text=True,
            )
        self.assertEqual(loaded.returncode, 0, loaded.stderr)
        self.assertNotEqual(missing.returncode, 0)


class KimiConfigTests(unittest.TestCase):
    def test_adds_loop_control_without_changing_model_config(self):
        source = """default_model = "radixark/k3"

[providers.radixark]
type = "openai"
base_url = "https://example.com/v1"
api_key = "secret#value"

[models."radixark/k3"]
provider = "radixark"
model = "k3"
max_context_size = 1000000
"""
        merged = kimi_config.merge_loop_control(source, 500, 5)
        parsed = tomllib.loads(merged)
        self.assertEqual(parsed["providers"], tomllib.loads(source)["providers"])
        self.assertEqual(parsed["models"], tomllib.loads(source)["models"])
        self.assertEqual(
            parsed["loop_control"],
            {"max_steps_per_turn": 500, "max_retries_per_step": 5},
        )

    def test_updates_existing_loop_control_and_preserves_other_values(self):
        source = """[loop_control]
"max_steps_per_turn" = 100 # keep this comment
unrelated = true

[providers.example]
type = "openai"
"""
        merged = kimi_config.merge_loop_control(source, 250, None)
        parsed = tomllib.loads(merged)
        self.assertEqual(parsed["loop_control"]["max_steps_per_turn"], 250)
        self.assertTrue(parsed["loop_control"]["unrelated"])
        self.assertIn("# keep this comment", merged)
        self.assertEqual(parsed["providers"]["example"]["type"], "openai")

    def test_rejects_invalid_source_config(self):
        with self.assertRaises(tomllib.TOMLDecodeError):
            kimi_config.merge_loop_control("[providers\n", 100, None)


if __name__ == "__main__":
    unittest.main()
