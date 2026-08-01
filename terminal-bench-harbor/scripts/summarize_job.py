from __future__ import annotations

import argparse
import json

from harbor_results import summarize


def print_human(summary: dict) -> None:
    status = "COMPLETE" if summary["complete"] else "INCOMPLETE"
    print(f"Job: {summary['job_name']}")
    print(f"Status: {status}")
    print(
        "Trials: "
        f"{summary['n_completed_trials']}/{summary['n_total_trials']} completed, "
        f"{summary['n_running_trials']} running, "
        f"{summary['n_pending_trials']} pending, "
        f"{summary['n_cancelled_trials']} cancelled"
    )
    print(
        f"Errors: {summary['n_errored_trials']}; retries: {summary['n_retries']}; "
        f"attempts per task: {summary['n_attempts']}"
    )
    for eval_summary in summary["evals"]:
        mean = eval_summary["mean"]
        mean_text = "n/a" if mean is None else f"{100 * mean:.2f}%"
        print(f"Eval: {eval_summary['key']}")
        print(
            f"Reward mean: {mean_text}; passed trials: {eval_summary['passed_trials']}; "
            f"failed trials: {eval_summary['failed_trials']}"
        )
        pass_at_attempts = eval_summary["pass_at_attempts"]
        if pass_at_attempts is not None:
            print(
                f"Pass@{summary['n_attempts']}: "
                f"{eval_summary['successful_tasks']}/{eval_summary['expected_tasks']} "
                f"({100 * pass_at_attempts:.2f}%)"
            )
        elif eval_summary["partial_pass_at_attempts_lower_bound"] is not None:
            lower = eval_summary["partial_pass_at_attempts_lower_bound"]
            print(
                f"Partial any-success lower bound: {eval_summary['successful_tasks']}/"
                f"{eval_summary['expected_tasks']} ({100 * lower:.2f}%); not final"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = summarize(args.job)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_human(summary)


if __name__ == "__main__":
    main()
