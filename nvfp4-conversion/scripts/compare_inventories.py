#!/usr/bin/env python3
import argparse
from pathlib import Path

from _common import load_json, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Require two checkpoint inventories to be identical.")
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    left = load_json(args.left)
    right = load_json(args.right)
    if left != right:
        left_files = {item["name"]: item for item in left.get("files", [])}
        right_files = {item["name"]: item for item in right.get("files", [])}
        shared = left_files.keys() & right_files.keys()
        changed = sorted(name for name in shared if left_files[name] != right_files[name])
        raise SystemExit(
            "inventory mismatch: "
            f"left_only={sorted(left_files.keys() - right_files.keys())}, "
            f"right_only={sorted(right_files.keys() - left_files.keys())}, changed={changed}"
        )
    write_json(
        args.output,
        {"identical": True, "file_count": left["file_count"], "total_file_bytes": left["total_file_bytes"]},
    )


if __name__ == "__main__":
    main()
