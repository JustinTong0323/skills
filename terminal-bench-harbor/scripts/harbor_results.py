from __future__ import annotations

import json
from pathlib import Path
from typing import Any


HARBOR_JOB_DEFAULTS = {"n_attempts": 1, "n_concurrent_trials": 4}


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
    return (
        result.get("finished_at") is not None
        and stats.get("n_completed_trials") == result.get("n_total_trials")
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
    for trial, reward in reward_trials(eval_result):
        task = trial_task(trial)
        entry = outcomes.setdefault(
            task, {"passed": False, "attempts": 0, "rewards": []}
        )
        entry["passed"] = entry["passed"] or is_passing_reward(reward)
        entry["attempts"] += 1
        entry["rewards"].append(reward)
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


def normalized_config(
    config: dict[str, Any], ignored_keys: set[str] | None = None
) -> dict[str, Any]:
    ignored = {"job_name"} if ignored_keys is None else ignored_keys
    resolved = {**HARBOR_JOB_DEFAULTS, **config}
    return {key: value for key, value in resolved.items() if key not in ignored}


def config_differences(left: Any, right: Any, path: str = "$") -> list[str]:
    if type(left) is not type(right):
        return [path]
    if isinstance(left, dict):
        differences: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left or key not in right:
                differences.append(child)
            else:
                differences.extend(config_differences(left[key], right[key], child))
        return differences
    if isinstance(left, list):
        differences = []
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                differences.append(child)
            else:
                differences.extend(config_differences(left[index], right[index], child))
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
) -> dict[str, Any]:
    pairs = reward_trials(eval_result)
    outcomes = task_outcomes(eval_result)
    attempt_counts = task_attempt_counts(eval_result)
    passed_trials = sum(is_passing_reward(reward) for _, reward in pairs)
    failed_trials = sum(not is_passing_reward(reward) for _, reward in pairs)
    successful_tasks = sum(entry["passed"] for entry in outcomes.values())
    denominator = expected_tasks or len(outcomes)
    lower_bound = successful_tasks / denominator if denominator else None
    exhausted_failures = sum(
        not outcomes.get(task, {}).get("passed", False) and count >= attempts
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
    avg_at_attempts = (
        sum(reward for _, reward in pairs) / expected_trials
        if complete and expected_trials
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
        "avg_at_attempts": avg_at_attempts,
        "pass_at_attempts": lower_bound if complete else None,
        "partial_pass_at_attempts_lower_bound": None if complete else lower_bound,
        "partial_pass_at_attempts_upper_bound": None if complete else upper_bound,
        "target_passes": target_passes,
        "target_reachable": target_reachable,
        "target_met": target_met,
        "harbor_pass_at_k": eval_result.get("pass_at_k", {}),
        "exception_stats": eval_result.get("exception_stats", {}),
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
    expected_tasks = expected_task_count(result, attempts, len(evals))
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
                expected_tasks,
                attempts,
                complete,
                target_passes,
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
        raise ValueError("result contains multiple evals; pass --eval-key")
    return next(iter(evals.items()))
