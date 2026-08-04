from __future__ import annotations

import argparse
import json

from harbor_results import config_differences, load_config, normalized_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument("--ignore-key", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    ignored_keys = {"job_name", *args.ignore_key}
    left = normalized_config(load_config(args.left), ignored_keys)
    right = normalized_config(load_config(args.right), ignored_keys)
    differences = config_differences(left, right)
    result = {
        "equivalent": not differences,
        "ignored_keys": sorted(ignored_keys),
        "different_paths": differences,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Equivalent: {result['equivalent']}")
        print(f"Ignored keys: {', '.join(result['ignored_keys'])}")
        if differences:
            print("Different paths:")
            for path in differences:
                print(f"  {path}")
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
