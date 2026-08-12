#!/usr/bin/env python3
import argparse
import hashlib
import subprocess
from pathlib import Path

from _common import canonical_json, load_json, sha256_file, write_json


def git_commit(path: Path) -> str:
    commit = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD^{commit}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise ValueError(f"Model Optimizer checkout is not clean: {path}")
    return commit


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an immutable NVFP4 conversion manifest from explicit evidence files.")
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--modelopt-root", type=Path, required=True)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--arguments", type=Path, required=True)
    parser.add_argument("--precision-contract", type=Path, required=True)
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    parser.add_argument("--source-repository")
    parser.add_argument("--source-revision")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    preflight = load_json(args.preflight)
    if preflight["decision"] not in {"whole_model", "routed_expert_streaming"}:
        raise ValueError(f"preflight has no executable decision: {preflight['decision']}")
    recipe_path = Path(args.recipe)
    if not recipe_path.is_absolute() and (args.modelopt_root / recipe_path).is_file():
        recipe_path = args.modelopt_root / recipe_path
    recipe = {"name": args.recipe}
    if recipe_path.is_file():
        recipe.update({"path": str(recipe_path.resolve()), "sha256": sha256_file(recipe_path)})
    artifacts = [
        {"name": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
        for path in sorted(args.artifact)
    ]
    payload = {
        "arguments": load_json(args.arguments),
        "architecture": preflight,
        "calibration": load_json(args.calibration),
        "conversion_artifacts": artifacts,
        "conversion_path": preflight["decision"],
        "environment": load_json(args.environment),
        "modelopt_commit": git_commit(args.modelopt_root),
        "precision_contract": load_json(args.precision_contract),
        "recipe": recipe,
        "source": {
            "inventory": load_json(args.source_inventory),
            "repository": args.source_repository,
            "revision": args.source_revision,
        },
        "topology": load_json(args.topology),
    }
    payload["manifest_sha256"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    write_json(args.output, payload)


if __name__ == "__main__":
    main()
