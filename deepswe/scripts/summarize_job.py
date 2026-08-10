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


def summarize(result, expected, eval_key=None):
    if expected <= 0:
        raise ValueError("expected task count must be positive")
    total = result.get("n_total_trials")
    if total != expected:
        raise ValueError(f"planned trial count is {total}, expected {expected}")

    selected_key, eval_result = select_eval(result, eval_key)
    rewards = parse_rewards(eval_result)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    parser.add_argument("--expected", type=int)
    parser.add_argument("--eval-key")
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
        summary = summarize(result, expected, args.eval_key)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_human(summary)
    if args.require_complete and not summary["complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
