from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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
uncompared_credential_paths = harbor_results.uncompared_credential_paths
compare_jobs = __import__("compare_jobs")
render_config = __import__("render_config")


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

    def test_empty_complete_eval_has_no_pass_rate(self) -> None:
        value = summarize_eval(
            "eval",
            {},
            expected_tasks=0,
            attempts=1,
            complete=True,
            target_passes=None,
        )
        self.assertTrue(value["score_valid"])
        self.assertIsNone(value["observed_pass_rate"])
        self.assertIsNone(value["pass_at_attempts"])

    def test_complete_summary_reports_pass_at_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(self.result()))
            (job / "config.json").write_text(
                json.dumps({"job_name": "test", "n_attempts": 2})
            )
            value = summarize(job)
        self.assertTrue(value["complete"])
        self.assertEqual(value["evals"][0]["avg_at_attempts"], 0.25)
        self.assertEqual(value["evals"][0]["pass_at_attempts"], 0.5)

    def test_summary_uses_harbor_omitted_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(self.result()))
            (job / "config.json").write_text(json.dumps({"job_name": "test"}))
            value = summarize(job)
        self.assertEqual(value["n_attempts"], 1)
        self.assertEqual(value["n_concurrent_trials"], 4)

    def test_summary_rejects_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(self.result()))
            with self.assertRaisesRegex(ValueError, "config.json is required"):
                summarize(job)

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

    def test_terminal_error_consumes_incomplete_target_slot(self) -> None:
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
        self.assertFalse(value["score_valid"])
        self.assertFalse(value["requires_rerun"])

    def test_retryable_error_consumes_aggregated_target_slot(self) -> None:
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

    def test_exception_only_task_is_retained(self) -> None:
        outcomes = task_outcomes(
            {
                "reward_stats": {"reward": {"1.0": ["task-a__one"]}},
                "exception_stats": {"RuntimeError": ["task-b__one"]},
            }
        )
        self.assertTrue(outcomes["task-b"]["error_only"])
        self.assertEqual(outcomes["task-b"]["attempts"], 1)
        self.assertEqual(outcomes["task-b"]["exception_types"], ["RuntimeError"])

    def test_agent_timeout_with_zero_reward_requires_rerun(self) -> None:
        value = summarize_eval(
            "eval",
            {
                "reward_stats": {"reward": {"0.0": ["task-a__one"]}},
                "exception_stats": {"AgentTimeoutError": ["task-a__one"]},
            },
            expected_tasks=1,
            attempts=1,
            complete=True,
            target_passes=None,
        )
        self.assertEqual(value["agent_timeout_tasks"], ["task-a"])
        self.assertTrue(value["requires_rerun"])
        self.assertFalse(value["score_valid"])
        self.assertIsNone(value["pass_at_attempts"])

    def test_agent_timeout_reward_is_not_a_capability_success(self) -> None:
        value = summarize_eval(
            "eval",
            {
                "reward_stats": {"reward": {"1.0": ["task-a__one"]}},
                "exception_stats": {"AgentTimeoutError": ["task-a__one"]},
            },
            expected_tasks=1,
            attempts=1,
            complete=True,
            target_passes=None,
        )
        self.assertEqual(value["successful_tasks"], 0)
        self.assertEqual(value["partial_pass_at_attempts_lower_bound"], 0.0)
        self.assertEqual(value["partial_pass_at_attempts_upper_bound"], 1.0)

    def test_non_timeout_attempt_preserves_capability_task_success(self) -> None:
        value = summarize_eval(
            "eval",
            {
                "reward_stats": {"reward": {"1.0": ["task-a__one", "task-a__two"]}},
                "exception_stats": {"AgentTimeoutError": ["task-a__one"]},
            },
            expected_tasks=1,
            attempts=2,
            complete=True,
            target_passes=None,
        )
        self.assertEqual(value["successful_tasks"], 1)
        self.assertEqual(value["partial_pass_at_attempts_lower_bound"], 1.0)

    def test_task_defined_timeout_keeps_deadline_score(self) -> None:
        value = summarize_eval(
            "eval",
            {
                "reward_stats": {"reward": {"0.0": ["task-a__one"]}},
                "exception_stats": {"AgentTimeoutError": ["task-a__one"]},
            },
            expected_tasks=1,
            attempts=1,
            complete=True,
            target_passes=None,
            capability_mode=False,
        )
        self.assertEqual(value["score_mode"], "task-defined deadline")
        self.assertTrue(value["score_valid"])
        self.assertFalse(value["requires_rerun"])
        self.assertEqual(value["pass_at_attempts"], 0.0)

    def test_summary_infers_timeout_policy_from_resolved_multiplier(self) -> None:
        result = self.result()
        result["stats"]["n_errored_trials"] = 1
        eval_result = result["stats"]["evals"]["agent__model__dataset"]
        eval_result["n_errors"] = 1
        eval_result["exception_stats"] = {"AgentTimeoutError": ["task-a__one"]}
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(result))
            config = {"job_name": "test", "n_attempts": 2}
            (job / "config.json").write_text(json.dumps(config))
            deadline = summarize(job)["evals"][0]
            config["agent_timeout_multiplier"] = 1_000_000_000.0
            (job / "config.json").write_text(json.dumps(config))
            capability = summarize(job)["evals"][0]
        self.assertEqual(deadline["score_mode"], "task-defined deadline")
        self.assertTrue(deadline["score_valid"])
        self.assertEqual(capability["score_mode"], "capability")
        self.assertFalse(capability["score_valid"])
        self.assertTrue(capability["requires_rerun"])

    def test_non_timeout_exception_remains_in_strict_score(self) -> None:
        value = summarize_eval(
            "eval",
            {
                "reward_stats": {"reward": {"0.0": ["task-a__one"]}},
                "exception_stats": {"NonZeroAgentExitCodeError": ["task-a__one"]},
            },
            expected_tasks=1,
            attempts=1,
            complete=True,
            target_passes=1,
        )
        self.assertTrue(value["score_valid"])
        self.assertFalse(value["requires_rerun"])
        self.assertEqual(value["pass_at_attempts"], 0.0)
        self.assertEqual(value["avg_at_attempts"], 0.0)
        self.assertEqual(value["exhausted_failed_tasks"], 1)
        self.assertEqual(value["optimistic_successful_tasks"], 0)
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

    def test_config_comparison_resolves_harbor_omitted_defaults(self) -> None:
        left = normalized_config({"job_name": "left"})
        right = normalized_config(
            {
                "job_name": "right",
                "n_attempts": 1,
                "n_concurrent_trials": 4,
            }
        )
        self.assertEqual(config_differences(left, right), [])

    def test_config_comparison_treats_persisted_credentials_as_unknown(self) -> None:
        fresh = {
            "agents": [
                {
                    "env": {
                        "OPENAI_API_KEY": "literal-value",
                        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "32768",
                    },
                    "kwargs": {"model_info": {"max_output_tokens": 32768}},
                }
            ]
        }
        persisted = {
            "agents": [
                {
                    "env": {
                        "OPENAI_API_KEY": "lite****lue",
                        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "****",
                    },
                    "kwargs": {"model_info": {"max_output_tokens": 32768}},
                }
            ]
        }
        self.assertEqual(
            config_differences(normalized_config(fresh), normalized_config(persisted)),
            [],
        )
        self.assertEqual(
            uncompared_credential_paths(fresh, persisted),
            [
                "$.agents[0].env.CLAUDE_CODE_MAX_OUTPUT_TOKENS",
                "$.agents[0].env.OPENAI_API_KEY",
            ],
        )

    def test_config_comparison_keeps_literal_sensitive_env_differences(self) -> None:
        left = normalized_config(
            {"agents": [{"env": {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "32768"}}]}
        )
        right = normalized_config(
            {"agents": [{"env": {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "65536"}}]}
        )
        self.assertEqual(
            config_differences(left, right),
            ["$.agents[0].env.CLAUDE_CODE_MAX_OUTPUT_TOKENS"],
        )
        self.assertEqual(uncompared_credential_paths(left, right), [])

    def test_config_comparison_canonicalizes_retry_exception_sets(self) -> None:
        left = normalized_config(
            {
                "retry": {
                    "include_exceptions": [
                        "RuntimeError",
                        "NetworkConnectionError",
                        "RuntimeError",
                    ],
                    "exclude_exceptions": ["AgentTimeoutError", "VerifierError"],
                }
            }
        )
        right = normalized_config(
            {
                "retry": {
                    "include_exceptions": ["NetworkConnectionError", "RuntimeError"],
                    "exclude_exceptions": ["VerifierError", "AgentTimeoutError"],
                }
            }
        )
        self.assertEqual(config_differences(left, right), [])

    def test_summary_uses_per_eval_task_counts(self) -> None:
        result = self.result()
        result["n_total_trials"] = 6
        result["stats"]["n_completed_trials"] = 6
        result["stats"]["evals"] = {
            "small": {
                "n_trials": 2,
                "n_errors": 0,
                "metrics": [{"mean": 1.0}],
                "reward_stats": {"reward": {"1.0": ["a__one", "b__one"]}},
            },
            "large": {
                "n_trials": 4,
                "n_errors": 0,
                "metrics": [{"mean": 0.5}],
                "reward_stats": {
                    "reward": {
                        "1.0": ["c__one", "d__one"],
                        "0.0": ["e__one", "f__one"],
                    }
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(result))
            (job / "config.json").write_text(json.dumps({"job_name": "test"}))
            value = summarize(job)
        summaries = {item["key"]: item for item in value["evals"]}
        self.assertEqual(summaries["small"]["expected_tasks"], 2)
        self.assertEqual(summaries["small"]["pass_at_attempts"], 1.0)
        self.assertEqual(summaries["large"]["expected_tasks"], 4)
        self.assertEqual(summaries["large"]["pass_at_attempts"], 0.5)

    def test_config_comparison_keeps_non_env_token_fields(self) -> None:
        left = normalized_config(
            {"agents": [{"kwargs": {"model_info": {"max_output_tokens": 32768}}}]}
        )
        right = normalized_config(
            {"agents": [{"kwargs": {"model_info": {"max_output_tokens": 65536}}}]}
        )
        self.assertEqual(
            config_differences(left, right),
            ["$.agents[0].kwargs.model_info.max_output_tokens"],
        )

    def test_pi_registry_credential_is_external_to_config_comparison(self) -> None:
        config = normalized_config(
            {
                "agents": [
                    {
                        "env": {
                            "TB_PI_MODELS_SEMANTIC_SHA256": "sha256:digest",
                        }
                    }
                ],
                "environment": {
                    "mounts": [{"source": "/runner/models.sha256-digest.json"}]
                },
            }
        )
        self.assertEqual(
            uncompared_credential_paths(config, config),
            ["$.pi_registry.apiKey"],
        )

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

    def test_summary_cli_handles_unknown_partial_denominator(self) -> None:
        result = self.result(finished=False)
        result["n_total_trials"] = 3
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(result))
            (job / "config.json").write_text(
                json.dumps({"job_name": "test", "n_attempts": 2})
            )
            process = subprocess.run(
                [sys.executable, str(SCRIPTS / "summarize_job.py"), str(job)],
                capture_output=True,
                text=True,
            )
        self.assertEqual(process.returncode, 0)
        self.assertIn("Observed Pass@2 rate", process.stdout)
        self.assertIn("final denominator unknown", process.stdout)
        self.assertIn("Final task-defined deadline score", process.stdout)
        self.assertIn("job incomplete", process.stdout)
        self.assertNotIn("rerun required", process.stdout)

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
        self.assertIsNone(value["config"]["credential_values_compared"])

    def test_compare_jobs_separates_shared_exception_only_tasks(self) -> None:
        result = self.result()
        eval_result = result["stats"]["evals"]["agent__model__dataset"]
        eval_result["exception_stats"] = {"RuntimeError": ["task-b__one"]}
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(json.dumps(result))
            (job / "config.json").write_text(json.dumps({"job_name": "test"}))
            value = compare_jobs.compare(
                str(job), str(job), None, None, allow_partial=False
            )
        self.assertEqual(value["counts"]["both_exception"], 1)
        self.assertEqual(value["tasks"]["both_exception"], ["task-b"])
        self.assertEqual(value["counts"]["both_fail"], 0)
        self.assertEqual(sum(value["counts"].values()), 2)


class RenderConfigTests(unittest.TestCase):
    def renderer_command(self, agent: str, directory: Path) -> list[str]:
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
        return command

    def run_renderer(self, agent: str, directory: Path) -> tuple[dict, dict | None]:
        output = directory / "job.json"
        command = self.renderer_command(agent, directory)
        subprocess.run(command, check=True)
        job = json.loads(output.read_text())
        model_config = None
        if agent == "pi":
            models = Path(job["environment"]["mounts"][0]["source"])
            model_config = json.loads(models.read_text())
        return job, model_config

    def test_terminus_uses_openai_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job, _ = self.run_renderer("terminus-2", Path(directory))
        self.assertEqual(
            job["agents"][0]["kwargs"]["api_base"], "http://server:30000/v1"
        )
        self.assertEqual(job["agents"][0]["model_name"], "openai/org/model")
        self.assertEqual(job["agents"][0]["env"]["OPENAI_API_KEY"], "EMPTY")
        self.assertEqual(job["agent_timeout_multiplier"], 1_000_000_000.0)

    def test_task_defined_timeout_policy_omits_multiplier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = self.renderer_command("terminus-2", Path(directory))
            command.extend(("--agent-timeout-policy", "task-defined"))
            subprocess.run(command, check=True)
            job = json.loads((Path(directory) / "job.json").read_text())
        self.assertNotIn("agent_timeout_multiplier", job)

    def test_task_defined_timeout_policy_rejects_multiplier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = self.renderer_command("terminus-2", Path(directory))
            process = subprocess.run(
                [
                    *command,
                    "--agent-timeout-policy",
                    "task-defined",
                    "--agent-timeout-multiplier",
                    "2",
                ],
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(process.returncode, 0)

    def test_rejects_invalid_sampling_values(self) -> None:
        cases = (
            ("--temperature", "nan"),
            ("--temperature", "-0.1"),
            ("--top-p", "nan"),
            ("--top-p", "0"),
            ("--top-p", "1.1"),
        )
        for option, value in cases:
            with self.subTest(option=option, value=value):
                with tempfile.TemporaryDirectory() as directory:
                    command = self.renderer_command("terminus-2", Path(directory))
                    process = subprocess.run(
                        [*command, option, value], capture_output=True, text=True
                    )
                self.assertNotEqual(process.returncode, 0)

    def test_claude_uses_server_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job, _ = self.run_renderer("claude-code", Path(directory))
        self.assertEqual(
            job["agents"][0]["env"]["ANTHROPIC_BASE_URL"], "http://server:30000"
        )
        self.assertEqual(job["agents"][0]["env"]["CLAUDE_CODE_ATTRIBUTION_HEADER"], "0")

    def test_pi_writes_models_and_mount(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job, models = self.run_renderer("pi", root)
            source = Path(job["environment"]["mounts"][0]["source"])
            self.assertFalse((root / "models.json").exists())
            self.assertTrue(source.exists())
        self.assertIsNotNone(models)
        self.assertEqual(models["providers"]["sglang"]["api"], "openai-completions")
        self.assertIn(".sha256-", source.name)
        self.assertEqual(
            job["agents"][0]["env"]["TB_PI_MODELS_SEMANTIC_SHA256"],
            "sha256:" + source.stem.rsplit(".sha256-", 1)[1],
        )
        self.assertEqual(
            job["environment"]["mounts"][0]["target"], "/root/.pi/agent/models.json"
        )

    def test_pi_registry_path_changes_with_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, _ = self.run_renderer("pi", root)
            command = self.renderer_command("pi", root)
            index = command.index("--max-output-tokens") + 1
            command[index] = "64000"
            subprocess.run(command, check=True)
            second = json.loads((root / "job.json").read_text())
            first_source = first["environment"]["mounts"][0]["source"]
            second_source = second["environment"]["mounts"][0]["source"]
        self.assertNotEqual(first_source, second_source)

    def test_pi_accepts_provider_prefixed_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = self.renderer_command("pi", root)
            model_index = command.index("--model") + 1
            command[model_index] = "sglang/org/model"
            subprocess.run(command, check=True)
            job = json.loads((root / "job.json").read_text())
            models_path = Path(job["environment"]["mounts"][0]["source"])
            models = json.loads(models_path.read_text())
        self.assertEqual(job["agents"][0]["model_name"], "sglang/org/model")
        self.assertEqual(models["providers"]["sglang"]["models"][0]["id"], "org/model")

    def test_pi_registry_refuses_different_credential_at_same_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = self.renderer_command("pi", root)
            subprocess.run([*command, "--api-key", "first"], check=True)
            process = subprocess.run(
                [*command, "--api-key", "second"], capture_output=True, text=True
            )
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("already exists with different content", process.stderr)

    def test_immutable_json_publish_failure_leaves_no_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "models.json"
            with mock.patch.object(render_config.os, "link", side_effect=OSError):
                with self.assertRaises(OSError):
                    render_config.write_json(
                        destination, {"providers": {}}, overwrite=False
                    )
            temporary_files = list(root.glob(".models.json.*.tmp"))
        self.assertFalse(destination.exists())
        self.assertEqual(temporary_files, [])

    def test_pi_rejects_aliased_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "job.json"
            command = self.renderer_command("pi", root)
            models_index = command.index("--pi-models-path") + 1
            command[models_index] = str(output)
            process = subprocess.run(command, capture_output=True, text=True)
            self.assertFalse(output.exists())
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("must refer to different files", process.stderr)

    def test_pi_rejects_output_at_content_addressed_registry_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = self.renderer_command("pi", root)
            subprocess.run(command, check=True)
            job = json.loads((root / "job.json").read_text())
            registry = Path(job["environment"]["mounts"][0]["source"])
            max_tokens_index = command.index("--max-output-tokens") + 1
            command[max_tokens_index] = "64000"
            output_index = command.index("--output") + 1
            command[output_index] = str(registry)
            process = subprocess.run(command, capture_output=True, text=True)
            models = json.loads(registry.read_text())
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("content-addressed Pi registry", process.stderr)
        self.assertIn("providers", models)


if __name__ == "__main__":
    unittest.main()
