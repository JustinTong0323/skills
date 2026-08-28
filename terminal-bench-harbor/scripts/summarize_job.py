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
        print(f"Score mode: {eval_summary['score_mode']}")
        print(
            f"Raw Harbor reward mean: {mean_text}; "
            f"passed reward records: {eval_summary['passed_trials']}; "
            f"failed reward records: {eval_summary['failed_trials']}"
        )
        print(
            f"Graded reward records: {eval_summary['graded_trials']}/"
            f"{eval_summary['expected_trials']}; "
            f"ungraded: {eval_summary['ungraded_trials']}"
        )
        if eval_summary["requires_rerun"]:
            print(
                "AgentTimeoutError tasks require rerun: "
                + ", ".join(eval_summary["agent_timeout_tasks"])
            )
        elif eval_summary["agent_timeout_tasks"]:
            print(
                "AgentTimeoutError task-defined deadline outcomes: "
                + ", ".join(eval_summary["agent_timeout_tasks"])
            )
        score_label = f"Final {eval_summary['score_mode']} score"
        if eval_summary["requires_rerun"]:
            print(f"{score_label}: unavailable; AgentTimeoutError rerun required")
        elif not eval_summary["score_valid"]:
            print(f"{score_label}: unavailable; job incomplete")
        avg_at_attempts = eval_summary["avg_at_attempts"]
        if avg_at_attempts is not None:
            print(f"Avg@{summary['n_attempts']}: {100 * avg_at_attempts:.2f}%")
        pass_at_attempts = eval_summary["pass_at_attempts"]
        if pass_at_attempts is not None:
            print(
                f"Pass@{summary['n_attempts']}: "
                f"{eval_summary['successful_tasks']}/{eval_summary['expected_tasks']} "
                f"({100 * pass_at_attempts:.2f}%)"
            )
        elif (
            eval_summary["partial_pass_at_attempts_lower_bound"] is not None
            and eval_summary["partial_pass_at_attempts_upper_bound"] is not None
        ):
            lower = eval_summary["partial_pass_at_attempts_lower_bound"]
            upper = eval_summary["partial_pass_at_attempts_upper_bound"]
            print(
                f"Partial Pass@{summary['n_attempts']} range: "
                f"{eval_summary['successful_tasks']}-"
                f"{eval_summary['optimistic_successful_tasks']}/"
                f"{eval_summary['expected_tasks']} "
                f"({100 * lower:.2f}%-{100 * upper:.2f}%); not final"
            )
        elif eval_summary["observed_pass_rate"] is not None:
            observed = eval_summary["observed_pass_rate"]
            print(
                f"Observed Pass@{summary['n_attempts']} rate: "
                f"{eval_summary['successful_tasks']}/{eval_summary['observed_tasks']} "
                f"({100 * observed:.2f}%); final denominator unknown"
            )
        if eval_summary["target_passes"] is not None:
            reachable = eval_summary["target_reachable"]
            state = (
                "reachable"
                if reachable is True
                else "unreachable"
                if reachable is False
                else "unknown"
            )
            print(
                f"Target: {eval_summary['target_passes']} passes; {state}; "
                f"optimistic ceiling: {eval_summary['optimistic_successful_tasks']}"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--target-passes", type=int)
    parser.add_argument("--fail-if-target-unreachable", action="store_true")
    args = parser.parse_args()
    if args.fail_if_target_unreachable and args.target_passes is None:
        parser.error("--fail-if-target-unreachable requires --target-passes")
    try:
        summary = summarize(args.job, target_passes=args.target_passes)
    except ValueError as error:
        parser.error(str(error))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_human(summary)
    if args.fail_if_target_unreachable and any(
        item["target_reachable"] is False for item in summary["evals"]
    ):
        raise SystemExit(3)


if __name__ == "__main__":
    main()
