#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path


def expected_ids(path):
    path = Path(path)
    if path.suffix == ".csv":
        with path.open(newline="") as file:
            return [row["instance_id"] for row in csv.DictReader(file)]
    return [
        json.loads(line)["instance_id"]
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def load_predictions(path):
    path = Path(path)
    if path.is_file():
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return list(data.values())
        return data
    preds = path / "preds.json"
    if preds.exists():
        return list(json.loads(preds.read_text()).values())
    records = []
    for pred in sorted(path.glob("instance_*/*.pred")):
        records.append(json.loads(pred.read_text()))
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--expected", required=True)
    args = parser.parse_args()

    expected = expected_ids(args.expected)
    expected_set = set(expected)
    if len(expected) != len(expected_set):
        raise ValueError("Expected input contains duplicate instance IDs")

    patches = []
    seen = set()
    empty = []
    unknown = []
    for record in load_predictions(args.input):
        instance_id = record.get("instance_id")
        if not instance_id:
            raise ValueError("Prediction is missing instance_id")
        if instance_id in seen:
            raise ValueError(f"Duplicate prediction: {instance_id}")
        seen.add(instance_id)
        if instance_id not in expected_set:
            unknown.append(instance_id)
            continue
        patch = record.get("model_patch", record.get("patch", "")) or ""
        if not patch.strip():
            empty.append(instance_id)
            continue
        patches.append(
            {"instance_id": instance_id, "patch": patch, "prefix": args.prefix}
        )

    if unknown:
        raise ValueError(f"Unknown instance IDs: {unknown[:10]}")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(patches, indent=2) + "\n")
    missing = [
        instance_id
        for instance_id in expected
        if instance_id not in {p["instance_id"] for p in patches}
    ]
    print(
        json.dumps(
            {
                "expected": len(expected),
                "records": len(seen),
                "nonempty": len(patches),
                "empty": len(empty),
                "missing": len(missing),
                "output": str(output),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
