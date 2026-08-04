from __future__ import annotations

import argparse
import json

from harbor_results import (
    config_differences,
    is_complete,
    load_job,
    normalized_config,
    select_eval,
    task_outcomes,
)


def compare(
    left_path: str,
    right_path: str,
    left_eval_key: str | None,
    right_eval_key: str | None,
    allow_partial: bool,
) -> dict:
    left_result, left_config, _ = load_job(left_path)
    right_result, right_config, _ = load_job(right_path)
    if not allow_partial and (
        not is_complete(left_result) or not is_complete(right_result)
    ):
        raise ValueError(
            "both jobs must be complete; pass --allow-partial to inspect partial data"
        )
    left_key, left_eval = select_eval(left_result, left_eval_key)
    right_key, right_eval = select_eval(right_result, right_eval_key)
    left = task_outcomes(left_eval)
    right = task_outcomes(right_eval)
    common = sorted(set(left) & set(right))
    categories = {
        "both_pass": [
            task for task in common if left[task]["passed"] and right[task]["passed"]
        ],
        "both_fail": [
            task
            for task in common
            if not left[task]["passed"] and not right[task]["passed"]
        ],
        "left_only": [
            task
            for task in common
            if left[task]["passed"] and not right[task]["passed"]
        ],
        "right_only": [
            task
            for task in common
            if not left[task]["passed"] and right[task]["passed"]
        ],
        "missing_left": sorted(set(right) - set(left)),
        "missing_right": sorted(set(left) - set(right)),
    }
    ignored_config_keys = {"job_name"}
    config_diff = config_differences(
        normalized_config(left_config, ignored_config_keys),
        normalized_config(right_config, ignored_config_keys),
    )
    return {
        "left": {"job_name": left_config.get("job_name"), "eval": left_key},
        "right": {"job_name": right_config.get("job_name"), "eval": right_key},
        "complete": {
            "left": is_complete(left_result),
            "right": is_complete(right_result),
        },
        "config": {
            "equivalent": not config_diff,
            "ignored_keys": sorted(ignored_config_keys),
            "different_paths": config_diff,
        },
        "counts": {name: len(tasks) for name, tasks in categories.items()},
        "tasks": categories,
    }


def print_human(result: dict) -> None:
    print(f"Left: {result['left']['job_name']} ({result['left']['eval']})")
    print(f"Right: {result['right']['job_name']} ({result['right']['eval']})")
    config = result["config"]
    print(
        "Config equivalent ignoring "
        f"{', '.join(config['ignored_keys'])}: {config['equivalent']}"
    )
    if config["different_paths"]:
        print("Config differences: " + ", ".join(config["different_paths"]))
    for name in (
        "both_pass",
        "both_fail",
        "left_only",
        "right_only",
        "missing_left",
        "missing_right",
    ):
        tasks = result["tasks"][name]
        print(f"{name}: {len(tasks)}")
        if tasks:
            print("  " + "\n  ".join(tasks))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument("--left-eval-key")
    parser.add_argument("--right-eval-key")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = compare(
            args.left,
            args.right,
            args.left_eval_key,
            args.right_eval_key,
            args.allow_partial,
        )
    except ValueError as error:
        parser.error(str(error))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print_human(result)


if __name__ == "__main__":
    main()
