from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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


def task_outcomes(eval_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    outcomes: dict[str, dict[str, Any]] = {}
    for trial, reward in reward_trials(eval_result):
        task = trial_task(trial)
        entry = outcomes.setdefault(
            task, {"passed": False, "attempts": 0, "rewards": []}
        )
        entry["passed"] = entry["passed"] or reward > 0
        entry["attempts"] += 1
        entry["rewards"].append(reward)
    return outcomes


def expected_task_count(
    result: dict[str, Any], config: dict[str, Any], eval_count: int
) -> int | None:
    attempts = int(config.get("n_attempts", 1))
    total = result.get("n_total_trials")
    divisor = attempts * max(eval_count, 1)
    if not isinstance(total, int) or total % divisor:
        return None
    return total // divisor


def summarize_eval(
    key: str,
    eval_result: dict[str, Any],
    expected_tasks: int | None,
    complete: bool,
) -> dict[str, Any]:
    pairs = reward_trials(eval_result)
    outcomes = task_outcomes(eval_result)
    passed_trials = sum(reward > 0 for _, reward in pairs)
    failed_trials = sum(reward <= 0 for _, reward in pairs)
    successful_tasks = sum(entry["passed"] for entry in outcomes.values())
    denominator = expected_tasks or len(outcomes)
    lower_bound = successful_tasks / denominator if denominator else None
    return {
        "key": key,
        "n_trials": eval_result.get("n_trials"),
        "n_errors": eval_result.get("n_errors"),
        "mean": (eval_result.get("metrics") or [{}])[0].get("mean"),
        "passed_trials": passed_trials,
        "failed_trials": failed_trials,
        "observed_tasks": len(outcomes),
        "successful_tasks": successful_tasks,
        "expected_tasks": expected_tasks,
        "pass_at_attempts": lower_bound if complete else None,
        "partial_pass_at_attempts_lower_bound": None if complete else lower_bound,
        "harbor_pass_at_k": eval_result.get("pass_at_k", {}),
        "exception_stats": eval_result.get("exception_stats", {}),
    }


def summarize(path: str | Path) -> dict[str, Any]:
    result, config, result_path = load_job(path)
    stats = result.get("stats", {})
    evals = stats.get("evals", {})
    complete = is_complete(result)
    expected_tasks = expected_task_count(result, config, len(evals))
    return {
        "path": str(result_path),
        "id": result.get("id"),
        "job_name": config.get("job_name", result_path.parent.name),
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
        "complete": complete,
        "n_attempts": int(config.get("n_attempts", 1)),
        "n_concurrent_trials": config.get("n_concurrent_trials"),
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
            summarize_eval(key, eval_result, expected_tasks, complete)
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
