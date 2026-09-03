from __future__ import annotations

import json
from pathlib import Path
from typing import Any


HARBOR_JOB_DEFAULTS = {"n_attempts": 1, "n_concurrent_trials": 4}
SENSITIVE_ENV_MARKERS = ("key", "secret", "token", "password", "credential", "auth")
INFRASTRUCTURE_EXCEPTIONS = frozenset(
    {"AgentSetupTimeoutError", "EnvironmentStartTimeoutError", "VerifierTimeoutError"}
)
TRIAL_TASK_NAME_LIMIT = 32


def load_job(path: str | Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    candidate = Path(path)
    result_path = candidate / "result.json" if candidate.is_dir() else candidate
    config_path = result_path.parent / "config.json"
    with result_path.open() as result_file:
        result = json.load(result_file)
    config: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open() as config_file:
            config = json.load(config_file)
    return result, config, result_path


def is_complete(result: dict[str, Any]) -> bool:
    stats = result.get("stats", {})
    completed = stats.get("n_completed_trials")
    total = result.get("n_total_trials")
    return (
        result.get("finished_at") is not None
        and isinstance(completed, int)
        and isinstance(total, int)
        and completed == total
        and stats.get("n_running_trials", 0) == 0
        and stats.get("n_pending_trials", 0) == 0
        and stats.get("n_cancelled_trials", 0) == 0
    )


def trial_task(trial_name: str) -> str:
    return trial_name.rsplit("__", 1)[0]


def reward_trials(eval_result: dict[str, Any]) -> list[tuple[str, float]]:
    pairs: list[tuple[str, float]] = []
    rewards = eval_result.get("reward_stats", {}).get("reward", {})
    for reward_text, trials in rewards.items():
        try:
            reward = float(reward_text)
        except (TypeError, ValueError):
            continue
        pairs.extend((trial, reward) for trial in trials)
    return pairs


def is_passing_reward(reward: float) -> bool:
    return reward == 1


def task_outcomes(eval_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    outcomes: dict[str, dict[str, Any]] = {}
    trial_names: dict[str, set[str]] = {}
    exception_types: dict[str, set[str]] = {}
    passing_trials: dict[str, set[str]] = {}
    agent_timeout_trials: set[str] = set()
    for trial, reward in reward_trials(eval_result):
        task = trial_task(trial)
        entry = outcomes.setdefault(
            task, {"passed": False, "attempts": 0, "rewards": []}
        )
        entry["passed"] = entry["passed"] or is_passing_reward(reward)
        entry["rewards"].append(reward)
        trial_names.setdefault(task, set()).add(trial)
        if is_passing_reward(reward):
            passing_trials.setdefault(task, set()).add(trial)
    for exception_type, trials in eval_result.get("exception_stats", {}).items():
        if exception_type == "CancelledError":
            continue
        for trial in trials:
            task = trial_task(trial)
            outcomes.setdefault(task, {"passed": False, "attempts": 0, "rewards": []})
            trial_names.setdefault(task, set()).add(trial)
            exception_types.setdefault(task, set()).add(exception_type)
            if exception_type == "AgentTimeoutError":
                agent_timeout_trials.add(trial)
    for task, entry in outcomes.items():
        entry["attempts"] = len(trial_names[task])
        entry["error_only"] = not entry["rewards"]
        entry["exception_types"] = sorted(exception_types.get(task, set()))
        entry["non_timeout_passed"] = any(
            trial not in agent_timeout_trials
            for trial in passing_trials.get(task, set())
        )
    return outcomes


def task_attempt_counts(eval_result: dict[str, Any]) -> dict[str, int]:
    trial_names = {trial for trial, _ in reward_trials(eval_result)}
    for exception_type, trials in eval_result.get("exception_stats", {}).items():
        if exception_type != "CancelledError":
            trial_names.update(trials)

    counts: dict[str, int] = {}
    for trial in trial_names:
        task = trial_task(trial)
        counts[task] = counts.get(task, 0) + 1
    return counts


def load_config(path: str | Path) -> dict[str, Any]:
    candidate = Path(path)
    config_path = candidate / "config.json" if candidate.is_dir() else candidate
    with config_path.open() as config_file:
        return json.load(config_file)


def is_sensitive_env_key(key: str) -> bool:
    return any(marker in key.casefold() for marker in SENSITIVE_ENV_MARKERS)


def is_persisted_credential_value(value: Any) -> bool:
    return isinstance(value, str) and (
        value in {"****", "[REDACTED]"}
        or (value.startswith("${") and value.endswith("}"))
        or (len(value) == 11 and value[4:8] == "****")
    )


def has_external_pi_registry_credential(config: dict[str, Any]) -> bool:
    return any(
        isinstance(agent, dict)
        and isinstance(agent.get("env"), dict)
        and "TB_PI_MODELS_SEMANTIC_SHA256" in agent["env"]
        for agent in config.get("agents", [])
    )


def uncompared_credential_paths(
    left: Any, right: Any, path: str = "$", *, environment: bool = False
) -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        paths = (
            ["$.pi_registry.apiKey"]
            if path == "$"
            and (
                has_external_pi_registry_credential(left)
                or has_external_pi_registry_credential(right)
            )
            else []
        )
        for key in sorted(set(left) & set(right)):
            child = f"{path}.{key}"
            if (
                environment
                and is_sensitive_env_key(key)
                and (
                    is_persisted_credential_value(left[key])
                    or is_persisted_credential_value(right[key])
                )
            ):
                paths.append(child)
            else:
                paths.extend(
                    uncompared_credential_paths(
                        left[key], right[key], child, environment=key == "env"
                    )
                )
        return paths
    if isinstance(left, list) and isinstance(right, list):
        paths = []
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            paths.extend(
                uncompared_credential_paths(
                    left_item,
                    right_item,
                    f"{path}[{index}]",
                    environment=environment,
                )
            )
        return paths
    return []


def normalized_config(
    config: dict[str, Any], ignored_keys: set[str] | None = None
) -> dict[str, Any]:
    ignored = {"job_name"} if ignored_keys is None else ignored_keys
    resolved = {**HARBOR_JOB_DEFAULTS, **config}
    retry = resolved.get("retry")
    if isinstance(retry, dict):
        retry = {**retry}
        for key in ("include_exceptions", "exclude_exceptions"):
            if isinstance(retry.get(key), list):
                retry[key] = sorted(set(retry[key]))
        resolved["retry"] = retry
    return {key: value for key, value in resolved.items() if key not in ignored}


def config_differences(
    left: Any, right: Any, path: str = "$", *, environment: bool = False
) -> list[str]:
    if type(left) is not type(right):
        return [path]
    if isinstance(left, dict):
        differences: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left or key not in right:
                differences.append(child)
            elif (
                environment
                and is_sensitive_env_key(key)
                and (
                    is_persisted_credential_value(left[key])
                    or is_persisted_credential_value(right[key])
                )
            ):
                continue
            else:
                differences.extend(
                    config_differences(
                        left[key], right[key], child, environment=key == "env"
                    )
                )
        return differences
    if isinstance(left, list):
        differences = []
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                differences.append(child)
            else:
                differences.extend(
                    config_differences(
                        left[index], right[index], child, environment=environment
                    )
                )
        return differences
    return [] if left == right else [path]


def expected_task_count(
    result: dict[str, Any], attempts: int, eval_count: int
) -> int | None:
    total = result.get("n_total_trials")
    divisor = attempts * max(eval_count, 1)
    if not isinstance(total, int) or total % divisor:
        return None
    return total // divisor


def summarize_eval(
    key: str,
    eval_result: dict[str, Any],
    expected_tasks: int | None,
    attempts: int,
    complete: bool,
    target_passes: int | None,
    capability_mode: bool = True,
) -> dict[str, Any]:
    pairs = reward_trials(eval_result)
    outcomes = task_outcomes(eval_result)
    attempt_counts = task_attempt_counts(eval_result)
    exception_tasks = sorted(
        task for task, outcome in outcomes.items() if outcome["exception_types"]
    )
    agent_timeout_tasks = sorted(
        task
        for task, outcome in outcomes.items()
        if "AgentTimeoutError" in outcome["exception_types"]
    )
    infrastructure_tasks = sorted(
        task
        for task, outcome in outcomes.items()
        if INFRASTRUCTURE_EXCEPTIONS.intersection(outcome["exception_types"])
    )
    rerun_tasks = set(infrastructure_tasks)
    if capability_mode:
        rerun_tasks.update(agent_timeout_tasks)
    passed_trials = sum(is_passing_reward(reward) for _, reward in pairs)
    failed_trials = sum(not is_passing_reward(reward) for _, reward in pairs)
    success_key = "non_timeout_passed" if capability_mode else "passed"
    successful_tasks = sum(entry[success_key] for entry in outcomes.values())
    observed_rate = successful_tasks / len(outcomes) if outcomes else None
    lower_bound = successful_tasks / expected_tasks if expected_tasks else None
    exhausted_failures = sum(
        not outcomes.get(task, {}).get(success_key, False)
        and task not in rerun_tasks
        and count >= attempts
        for task, count in attempt_counts.items()
    )
    optimistic_successful_tasks = (
        expected_tasks - exhausted_failures if expected_tasks is not None else None
    )
    upper_bound = (
        optimistic_successful_tasks / expected_tasks
        if expected_tasks and optimistic_successful_tasks is not None
        else None
    )
    expected_trials = expected_tasks * attempts if expected_tasks is not None else None
    score_valid = complete and not rerun_tasks
    task_name_merge_risk = complete and (
        any(len(task) >= TRIAL_TASK_NAME_LIMIT for task in outcomes)
        or sum(entry["attempts"] for entry in outcomes.values())
        != len(outcomes) * attempts
    )
    avg_at_attempts = (
        sum(reward for _, reward in pairs) / expected_trials
        if score_valid and expected_trials
        else None
    )
    ungraded_trials = (
        max(expected_trials - len(pairs), 0) if expected_trials is not None else None
    )
    target_met = (
        successful_tasks >= target_passes if target_passes is not None else None
    )
    if target_met is True:
        target_reachable = True
    elif optimistic_successful_tasks is not None and target_passes is not None:
        target_reachable = optimistic_successful_tasks >= target_passes
    else:
        target_reachable = None
    return {
        "key": key,
        "n_trials": eval_result.get("n_trials"),
        "n_errors": eval_result.get("n_errors"),
        "mean": (eval_result.get("metrics") or [{}])[0].get("mean"),
        "passed_trials": passed_trials,
        "failed_trials": failed_trials,
        "graded_trials": len(pairs),
        "expected_trials": expected_trials,
        "ungraded_trials": ungraded_trials,
        "observed_tasks": len(outcomes),
        "successful_tasks": successful_tasks,
        "expected_tasks": expected_tasks,
        "exhausted_failed_tasks": exhausted_failures,
        "optimistic_successful_tasks": optimistic_successful_tasks,
        "observed_pass_rate": observed_rate,
        "avg_at_attempts": avg_at_attempts,
        "pass_at_attempts": lower_bound if score_valid else None,
        "partial_pass_at_attempts_lower_bound": None if score_valid else lower_bound,
        "partial_pass_at_attempts_upper_bound": None if score_valid else upper_bound,
        "target_passes": target_passes,
        "target_reachable": target_reachable,
        "target_met": target_met,
        "harbor_pass_at_k": eval_result.get("pass_at_k", {}),
        "exception_stats": eval_result.get("exception_stats", {}),
        "exception_tasks": exception_tasks,
        "agent_timeout_tasks": agent_timeout_tasks,
        "infrastructure_exception_tasks": infrastructure_tasks,
        "rerun_tasks": sorted(rerun_tasks),
        "task_name_merge_risk": task_name_merge_risk,
        "score_mode": "capability" if capability_mode else "task-defined deadline",
        "score_valid": score_valid,
        "requires_rerun": bool(rerun_tasks),
    }


def summarize(path: str | Path, target_passes: int | None = None) -> dict[str, Any]:
    if target_passes is not None and target_passes < 0:
        raise ValueError("target_passes must be non-negative")
    result, config, result_path = load_job(path)
    if not (result_path.parent / "config.json").exists():
        raise ValueError("config.json is required to recover job semantics")
    stats = result.get("stats", {})
    evals = stats.get("evals", {})
    complete = is_complete(result)
    attempts = config.get("n_attempts", HARBOR_JOB_DEFAULTS["n_attempts"])
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts <= 0:
        raise ValueError("config.json must contain a positive integer n_attempts")
    concurrency = config.get(
        "n_concurrent_trials", HARBOR_JOB_DEFAULTS["n_concurrent_trials"]
    )
    if (
        not isinstance(concurrency, int)
        or isinstance(concurrency, bool)
        or concurrency <= 0
    ):
        raise ValueError(
            "config.json must contain a positive integer n_concurrent_trials"
        )
    incomplete_expected_tasks = (
        expected_task_count(result, attempts, 1) if len(evals) == 1 else None
    )
    if (
        "agent_timeout_multiplier" in config
        and config["agent_timeout_multiplier"] is None
    ):
        raise ValueError(
            "config.json has agent_timeout_multiplier null (Harbor serialized an "
            "infinite multiplier); the timeout policy cannot be recovered"
        )
    capability_mode = config.get("agent_timeout_multiplier") is not None
    return {
        "path": str(result_path),
        "id": result.get("id"),
        "job_name": config.get("job_name", result_path.parent.name),
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
        "complete": complete,
        "n_attempts": attempts,
        "n_concurrent_trials": concurrency,
        "n_total_trials": result.get("n_total_trials"),
        "n_completed_trials": stats.get("n_completed_trials"),
        "n_running_trials": stats.get("n_running_trials"),
        "n_pending_trials": stats.get("n_pending_trials"),
        "n_cancelled_trials": stats.get("n_cancelled_trials"),
        "n_errored_trials": stats.get("n_errored_trials"),
        "n_retries": stats.get("n_retries"),
        "n_input_tokens": stats.get("n_input_tokens"),
        "n_cache_tokens": stats.get("n_cache_tokens"),
        "n_output_tokens": stats.get("n_output_tokens"),
        "cost_usd": stats.get("cost_usd"),
        "evals": [
            summarize_eval(
                key,
                eval_result,
                (
                    len(task_outcomes(eval_result))
                    if complete
                    else incomplete_expected_tasks
                ),
                attempts,
                complete,
                target_passes,
                capability_mode,
            )
            for key, eval_result in evals.items()
        ],
    }


def select_eval(result: dict[str, Any], key: str | None) -> tuple[str, dict[str, Any]]:
    evals = result.get("stats", {}).get("evals", {})
    if key is not None:
        if key not in evals:
            raise ValueError(f"eval key not found: {key}")
        return key, evals[key]
    if len(evals) != 1:
        raise ValueError(
            "result contains multiple evals; pass --left-eval-key/--right-eval-key"
        )
    return next(iter(evals.items()))
