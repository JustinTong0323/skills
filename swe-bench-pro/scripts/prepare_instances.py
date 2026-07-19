#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

import yaml
from datasets import load_dataset


def format_problem(row):
    return (
        f"{row['problem_statement']}\n\n"
        f"Requirements:\n{row['requirements']}\n\n"
        f"New interfaces introduced:\n{row['interface']}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="ScaleAI/SWE-bench_Pro")
    parser.add_argument("--split", default="test")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--image-repository", default="jefzda/sweap-images")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset(args.dataset, split=args.split)
    instances = []
    ids = []

    for row in dataset:
        instance_id = row["instance_id"]
        tag = row["dockerhub_tag"]
        if not tag or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", tag):
            raise ValueError(f"Invalid dockerhub_tag for {instance_id}: {tag!r}")
        ids.append(instance_id)
        instances.append(
            {
                "image_name": f"{args.image_repository}:{tag}",
                "problem_statement": format_problem(row),
                "instance_id": instance_id,
                "base_commit": row["base_commit"],
                "repo_name": "app",
            }
        )

    if len(ids) != len(set(ids)):
        raise ValueError("Dataset contains duplicate instance IDs")

    instances_path = output_dir / "instances.yaml"
    raw_path = output_dir / "raw_samples.jsonl"
    manifest_path = output_dir / "dataset_manifest.json"
    instances_path.write_text(yaml.safe_dump(instances, sort_keys=False))
    dataset.to_json(raw_path, orient="records", lines=True, force_ascii=False)
    manifest = {
        "dataset": args.dataset,
        "split": args.split,
        "count": len(ids),
        "fingerprint": dataset._fingerprint,
        "image_repository": args.image_repository,
        "instance_ids": ids,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "count": len(ids),
                "instances": str(instances_path),
                "raw_samples": str(raw_path),
                "manifest": str(manifest_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
