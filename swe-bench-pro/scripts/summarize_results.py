#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path


def load_expected(path):
    path = Path(path)
    if path.suffix == ".csv":
        with path.open(newline="") as file:
            return [row["instance_id"] for row in csv.DictReader(file)]
    return [
        json.loads(line)["instance_id"]
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-results", required=True)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--predictions")
    args = parser.parse_args()

    expected = load_expected(args.expected)
    expected_set = set(expected)
    if len(expected) != len(expected_set):
        raise ValueError("Expected input contains duplicate instance IDs")
    results = json.loads(Path(args.eval_results).read_text())
    unknown = sorted(set(results) - expected_set)
    if unknown:
        raise ValueError(f"Evaluation contains unknown IDs: {unknown[:10]}")
    submitted = None
    if args.predictions:
        predictions = json.loads(Path(args.predictions).read_text())
        if not isinstance(predictions, list):
            raise ValueError("Predictions must be a JSON list")
        submitted_ids = [record["instance_id"] for record in predictions]
        if len(submitted_ids) != len(set(submitted_ids)):
            raise ValueError("Predictions contain duplicate instance IDs")
        unknown_predictions = sorted(set(submitted_ids) - expected_set)
        if unknown_predictions:
            raise ValueError(
                f"Predictions contain unknown IDs: {unknown_predictions[:10]}"
            )
        submitted = len(submitted_ids)

    resolved = sum(value is True for value in results.values())
    unresolved = sum(value is False for value in results.values())
    invalid_results = sum(
        value is not True and value is not False for value in results.values()
    )
    evaluated = len(results)
    expected_count = len(expected)
    summary = {
        "resolved": resolved,
        "expected": expected_count,
        "strict_accuracy": resolved / expected_count if expected_count else 0,
        "evaluated": evaluated,
        "evaluated_accuracy": resolved / evaluated if evaluated else 0,
        "unresolved": unresolved,
        "submitted_nonempty": submitted,
        "missing_or_unevaluated": expected_count - evaluated,
        "invalid_results": invalid_results,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
