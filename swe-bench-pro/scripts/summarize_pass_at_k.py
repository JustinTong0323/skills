#!/usr/bin/env python3
import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def load_expected(path):
    path = Path(path)
    if path.suffix == ".csv":
        with path.open(newline="") as file:
            ids = [row["instance_id"] for row in csv.DictReader(file)]
    else:
        ids = [
            json.loads(line)["instance_id"]
            for line in path.read_text().splitlines()
            if line.strip()
        ]
    if len(ids) != len(set(ids)):
        raise ValueError("Expected input contains duplicate instance IDs")
    return ids


def summarize_run(run_dir, expected_ids):
    run_dir = Path(run_dir)
    expected = set(expected_ids)
    predictions = json.loads((run_dir / "patches.json").read_text())
    if not isinstance(predictions, list):
        raise ValueError(f"{run_dir}: patches.json must be a JSON list")
    submitted_ids = [item["instance_id"] for item in predictions]
    submitted = set(submitted_ids)
    if len(submitted_ids) != len(submitted):
        raise ValueError(f"{run_dir}: duplicate prediction IDs")
    unknown_predictions = sorted(submitted - expected)
    if unknown_predictions:
        raise ValueError(
            f"{run_dir}: unknown prediction IDs: {unknown_predictions[:10]}"
        )
    empty_patches = [
        item["instance_id"]
        for item in predictions
        if not isinstance(item.get("patch"), str) or not item["patch"].strip()
    ]
    if empty_patches:
        raise ValueError(f"{run_dir}: empty submitted patches: {empty_patches[:10]}")

    results_path = run_dir / "evaluation/eval_results.json"
    results = json.loads(results_path.read_text())
    if not isinstance(results, dict):
        raise ValueError(f"{run_dir}: eval_results.json must be a JSON object")
    missing_results = sorted(submitted - set(results))
    unexpected_results = sorted(set(results) - submitted)
    if missing_results:
        raise ValueError(
            f"{run_dir}: submitted predictions missing results: {missing_results[:10]}"
        )
    if unexpected_results:
        raise ValueError(
            f"{run_dir}: results without submitted predictions: {unexpected_results[:10]}"
        )
    invalid = sorted(key for key, value in results.items() if type(value) is not bool)
    if invalid:
        raise ValueError(f"{run_dir}: non-boolean results: {invalid[:10]}")

    resolved = {key for key, value in results.items() if value}
    evaluated = len(results)
    expected_count = len(expected_ids)
    summary = {
        "run": run_dir.name,
        "resolved": len(resolved),
        "expected": expected_count,
        "strict_accuracy": len(resolved) / expected_count if expected_count else 0,
        "evaluated": evaluated,
        "evaluated_accuracy": len(resolved) / evaluated if evaluated else 0,
        "unresolved": evaluated - len(resolved),
        "submitted_nonempty": len(submitted),
        "missing_or_unevaluated": expected_count - evaluated,
        "invalid_results": 0,
    }
    return summary, resolved


def build_report(run_dirs, expected_ids):
    resolved_run_dirs = [Path(run_dir).resolve() for run_dir in run_dirs]
    if len(resolved_run_dirs) != len(set(resolved_run_dirs)):
        raise ValueError("Run directories must be distinct")

    runs = []
    resolved_sets = []
    for run_dir in resolved_run_dirs:
        summary, resolved = summarize_run(run_dir, expected_ids)
        runs.append(summary)
        resolved_sets.append(resolved)
    if not runs:
        raise ValueError("At least one --run is required")

    union = set().union(*resolved_sets)
    histogram = Counter(
        sum(instance_id in resolved for resolved in resolved_sets)
        for instance_id in expected_ids
    )
    k = len(runs)
    score = len(union) / len(expected_ids) if expected_ids else 0
    return {
        "k": k,
        "expected": len(expected_ids),
        "runs": runs,
        "resolved_union": len(union),
        "empirical_pass_at_k": score,
        f"empirical_pass_at_{k}": score,
        "resolved_by_run_count": {
            str(count): histogram[count] for count in range(k + 1)
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", required=True)
    parser.add_argument("--run", action="append", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    report = build_report(args.run, load_expected(args.expected))
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
