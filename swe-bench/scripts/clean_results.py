#!/usr/bin/env python3
"""Clean SWE-bench results to allow rerunning failed instances.

Usage:
    python3 clean_results.py <results_dir> [--dry-run]

This script:
1. Deletes exit_statuses_*.yaml files (mini-swe-agent's completion tracker)
2. Filters preds.json to keep only entries with non-empty model_patch
3. Reports how many instances will be rerun

Without cleaning, mini-swe-agent skips failed instances because:
- exit_statuses_*.yaml marks them as "done" regardless of success/failure
- preds.json entries with empty patches block reruns
"""
import json
import glob
import sys
import os

def clean(results_dir, dry_run=False):
    # 1. Find and remove exit_statuses files
    exit_files = glob.glob(os.path.join(results_dir, "exit_statuses_*.yaml"))
    for f in exit_files:
        print(f"{'Would delete' if dry_run else 'Deleting'}: {f}")
        if not dry_run:
            os.remove(f)

    # 2. Filter preds.json
    preds_file = os.path.join(results_dir, "preds.json")
    if not os.path.exists(preds_file):
        print(f"No preds.json found in {results_dir}")
        return

    with open(preds_file) as f:
        data = json.load(f)

    total = len(data)
    good = {k: v for k, v in data.items()
            if v.get("model_patch") and v["model_patch"].strip()}
    removed = total - len(good)

    print(f"preds.json: {total} total, {len(good)} with patches, {removed} empty/failed")

    if not dry_run and removed > 0:
        with open(preds_file, "w") as f:
            json.dump(good, f, indent=2)
        print(f"Cleaned preds.json: kept {len(good)}, removed {removed}")

    # 3. Count trajectories
    traj_count = len([d for d in os.listdir(results_dir)
                      if os.path.isdir(os.path.join(results_dir, d))])
    to_rerun = 500 - len(good)
    print(f"\nTrajectory dirs: {traj_count}")
    print(f"Instances to rerun: {to_rerun}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 clean_results.py <results_dir> [--dry-run]")
        sys.exit(1)
    dry_run = "--dry-run" in sys.argv
    clean(sys.argv[1], dry_run=dry_run)
