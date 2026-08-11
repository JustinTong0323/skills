import argparse
import json
import math
from pathlib import Path


def load_result(path):
    source = Path(path)
    if source.is_dir():
        source = source / "result.json"
    value = json.loads(source.read_text())
    if not isinstance(value, dict):
        raise ValueError("result.json must contain a JSON object")
    return value


def select_eval(result, eval_key):
    evals = result.get("stats", {}).get("evals", {})
    if not isinstance(evals, dict) or not evals:
        raise ValueError("result has no evaluation groups")
    if eval_key is not None:
        if eval_key not in evals:
            raise ValueError(f"unknown evaluation group: {eval_key}")
        return eval_key, evals[eval_key]
    if len(evals) != 1:
        raise ValueError("result has multiple evaluation groups; pass --eval-key")
    return next(iter(evals.items()))


def parse_rewards(eval_result):
    buckets = eval_result.get("reward_stats", {}).get("reward", {})
    if not isinstance(buckets, dict):
        raise ValueError("reward_stats.reward must be a JSON object")
    rewards = {}
    for raw_reward, trial_names in buckets.items():
        try:
            reward = float(raw_reward)
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid reward value: {raw_reward!r}") from error
        if not math.isfinite(reward) or reward not in (0.0, 1.0):
            raise ValueError(f"DeepSWE reward must be binary, got {raw_reward!r}")
        if not isinstance(trial_names, list) or not all(
            isinstance(name, str) for name in trial_names
        ):
            raise ValueError(f"reward bucket {raw_reward!r} must contain trial names")
        for trial_name in trial_names:
            if trial_name in rewards:
                raise ValueError(
                    f"trial appears in multiple reward buckets: {trial_name}"
                )
            rewards[trial_name] = reward
    return rewards


def parse_exceptions(eval_result):
    buckets = eval_result.get("exception_stats", {})
    if not isinstance(buckets, dict):
        raise ValueError("exception_stats must be a JSON object")
    exceptions = {}
    for error_type, trial_names in buckets.items():
        if not isinstance(error_type, str):
            raise ValueError("exception type must be a string")
        if not isinstance(trial_names, list) or not all(
            isinstance(name, str) for name in trial_names
        ):
            raise ValueError(
                f"exception bucket {error_type!r} must contain trial names"
            )
        for trial_name in trial_names:
            if trial_name in exceptions:
                raise ValueError(
                    f"trial appears in multiple exception buckets: {trial_name}"
                )
            exceptions[trial_name] = error_type
    return exceptions


def task_id(trial_name):
    task, separator, attempt = trial_name.rpartition("__")
    if not separator or not task or not attempt:
        raise ValueError(f"trial name has no task/attempt suffix: {trial_name!r}")
    return task


def index_by_task(values, label):
    indexed = {}
    for trial_name, value in values.items():
        task = task_id(trial_name)
        if task in indexed:
            raise ValueError(f"{label} contains multiple trials for task: {task}")
        indexed[task] = value
    return indexed


def summarize(result, expected, eval_key=None):
    if expected <= 0:
        raise ValueError("expected task count must be positive")
    total = result.get("n_total_trials")
    if total != expected:
        raise ValueError(f"planned trial count is {total}, expected {expected}")

    selected_key, eval_result = select_eval(result, eval_key)
    rewards = parse_rewards(eval_result)
    index_by_task(rewards, "reward data")
    if len(rewards) > expected:
        raise ValueError("graded reward count exceeds expected tasks")

    stats = result.get("stats", {})
    completed = stats.get("n_completed_trials", stats.get("n_trials", 0))
    errored = stats.get("n_errored_trials", stats.get("n_errors", 0))
    running = stats.get("n_running_trials", 0)
    pending = stats.get("n_pending_trials", max(expected - completed - running, 0))
    cancelled = stats.get("n_cancelled_trials", 0)
    retries = stats.get("n_retries", 0)
    reward_sum = sum(rewards.values())
    graded = len(rewards)
    complete = (
        result.get("finished_at") is not None
        and completed == expected
        and running == 0
        and pending == 0
        and cancelled == 0
    )

    return {
        "eval_key": selected_key,
        "complete": complete,
        "resolved": sum(reward == 1.0 for reward in rewards.values()),
        "expected": expected,
        "strict_score": reward_sum / expected,
        "graded_rewards": graded,
        "observed_reward_mean": reward_sum / graded if graded else 0.0,
        "ungraded": expected - graded,
        "completed_trials": completed,
        "errored_trials": errored,
        "running_trials": running,
        "pending_trials": pending,
        "cancelled_trials": cancelled,
        "retries": retries,
    }


def overlay_recovery(
    result,
    recovery_result,
    expected,
    replacement_tasks,
    allowed_error_types,
    eval_key=None,
    recovery_eval_key=None,
):
    replacement_tasks = list(replacement_tasks)
    allowed_error_types = set(allowed_error_types)
    if not replacement_tasks:
        raise ValueError("recovery overlay requires at least one replacement task")
    if len(set(replacement_tasks)) != len(replacement_tasks):
        raise ValueError("replacement task list contains duplicates")
    if not allowed_error_types:
        raise ValueError("recovery overlay requires an allowed error type")

    original = summarize(result, expected, eval_key)
    recovery = summarize(
        recovery_result,
        len(replacement_tasks),
        recovery_eval_key,
    )
    if not original["complete"]:
        raise ValueError("original job is incomplete")
    if not recovery["complete"]:
        raise ValueError("recovery job is incomplete")
    if recovery["errored_trials"]:
        raise ValueError("recovery job contains errored trials")

    _, original_eval = select_eval(result, eval_key)
    original_rewards = index_by_task(parse_rewards(original_eval), "original rewards")
    original_exceptions = index_by_task(
        parse_exceptions(original_eval), "original exceptions"
    )
    overlap = set(original_rewards) & set(original_exceptions)
    if overlap:
        raise ValueError(
            f"original tasks have both rewards and exceptions: {', '.join(sorted(overlap))}"
        )

    requested = set(replacement_tasks)
    unknown = requested - set(original_exceptions)
    if unknown:
        raise ValueError(
            "replacement tasks are not original errors: " + ", ".join(sorted(unknown))
        )
    disallowed = {
        task: original_exceptions[task]
        for task in requested
        if original_exceptions[task] not in allowed_error_types
    }
    if disallowed:
        values = ", ".join(
            f"{task}={error_type}" for task, error_type in sorted(disallowed.items())
        )
        raise ValueError(f"replacement tasks have disallowed error types: {values}")

    _, recovery_eval = select_eval(recovery_result, recovery_eval_key)
    recovery_rewards = index_by_task(parse_rewards(recovery_eval), "recovery rewards")
    recovered = set(recovery_rewards)
    if recovered != requested:
        missing = ", ".join(sorted(requested - recovered)) or "none"
        unexpected = ", ".join(sorted(recovered - requested)) or "none"
        raise ValueError(
            f"recovery task mismatch; missing: {missing}; unexpected: {unexpected}"
        )
    if recovery["ungraded"]:
        raise ValueError("recovery job contains ungraded tasks")

    corrected_rewards = dict(original_rewards)
    corrected_rewards.update(recovery_rewards)
    reward_sum = sum(corrected_rewards.values())
    graded = len(corrected_rewards)
    corrected = {
        "complete": True,
        "resolved": sum(reward == 1.0 for reward in corrected_rewards.values()),
        "expected": expected,
        "strict_score": reward_sum / expected,
        "graded_rewards": graded,
        "observed_reward_mean": reward_sum / graded if graded else 0.0,
        "ungraded": expected - graded,
    }
    replacements = [
        {
            "task": task,
            "original_error_type": original_exceptions[task],
            "recovery_reward": recovery_rewards[task],
        }
        for task in sorted(requested)
    ]
    return {
        "original": original,
        "recovery": recovery,
        "corrected": corrected,
        "replacements": replacements,
    }


def print_human(summary):
    state = "COMPLETE" if summary["complete"] else "INCOMPLETE"
    print(f"Status: {state}")
    print(
        f"Resolved: {summary['resolved']}/{summary['expected']} "
        f"({100 * summary['strict_score']:.2f}%)"
    )
    print(
        f"Rewards: {summary['graded_rewards']} graded, "
        f"{summary['ungraded']} ungraded, "
        f"observed-only mean {100 * summary['observed_reward_mean']:.2f}%"
    )
    print(
        f"Trials: {summary['completed_trials']} completed, "
        f"{summary['errored_trials']} errored, "
        f"{summary['running_trials']} running, "
        f"{summary['pending_trials']} pending, "
        f"{summary['cancelled_trials']} cancelled, "
        f"{summary['retries']} retries"
    )


def print_recovery_human(summary):
    original = summary["original"]
    recovery = summary["recovery"]
    corrected = summary["corrected"]
    print(
        f"Original: {original['resolved']}/{original['expected']} "
        f"({100 * original['strict_score']:.2f}%), "
        f"{original['ungraded']} ungraded"
    )
    print(
        f"Recovery: {recovery['resolved']}/{recovery['expected']} "
        f"({100 * recovery['strict_score']:.2f}%)"
    )
    print(
        f"Corrected: {corrected['resolved']}/{corrected['expected']} "
        f"({100 * corrected['strict_score']:.2f}%), "
        f"{corrected['ungraded']} ungraded"
    )
    for replacement in summary["replacements"]:
        print(
            f"Replaced: {replacement['task']} "
            f"({replacement['original_error_type']} -> "
            f"reward {replacement['recovery_reward']:.0f})"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    parser.add_argument("--expected", type=int)
    parser.add_argument("--eval-key")
    parser.add_argument("--recovery-job")
    parser.add_argument("--recovery-eval-key")
    parser.add_argument("--replace-task", action="append", default=[])
    parser.add_argument("--allow-error-type", action="append", default=[])
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = load_result(args.job)
        expected = (
            args.expected if args.expected is not None else result.get("n_total_trials")
        )
        if not isinstance(expected, int):
            raise ValueError("expected task count is unavailable; pass --expected")
        recovery_options = (
            args.recovery_job,
            args.recovery_eval_key,
            args.replace_task,
            args.allow_error_type,
        )
        if args.recovery_job:
            recovery_result = load_result(args.recovery_job)
            summary = overlay_recovery(
                result,
                recovery_result,
                expected,
                args.replace_task,
                args.allow_error_type,
                args.eval_key,
                args.recovery_eval_key,
            )
        elif any(recovery_options[1:]):
            raise ValueError("recovery options require --recovery-job")
        else:
            summary = summarize(result, expected, args.eval_key)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    elif args.recovery_job:
        print_recovery_human(summary)
    else:
        print_human(summary)
    complete = (
        summary["corrected"]["complete"] if args.recovery_job else summary["complete"]
    )
    if args.require_complete and not complete:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
