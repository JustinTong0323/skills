#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

from _common import load_json, sha256_file, write_json


def lfs_sha256(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get("sha256")
    return getattr(value, "sha256", None)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a Hugging Face model revision against a local release inventory."
    )
    parser.add_argument("repo_id")
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--visibility", choices=["private", "public"], required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError as error:
        raise RuntimeError("huggingface_hub is required for remote verification") from error

    token = sys.stdin.readline().strip()
    os.environ.pop("HF_TOKEN", None)
    if not token:
        raise ValueError("read an empty Hugging Face token from stdin")
    expected = load_json(args.inventory)
    expected_files = {item["name"]: item for item in expected["files"]}
    api = HfApi(token=token)
    info = api.repo_info(args.repo_id, repo_type="model", revision=args.revision, files_metadata=True)
    actual_private = bool(info.private)
    expected_private = args.visibility == "private"
    if actual_private != expected_private:
        raise ValueError(f"visibility mismatch: expected={args.visibility}, actual_private={actual_private}")
    siblings = {item.rfilename: item for item in info.siblings}
    if set(siblings) != set(expected_files):
        raise ValueError(
            f"remote filename mismatch: remote_only={sorted(set(siblings) - set(expected_files))}, "
            f"local_only={sorted(set(expected_files) - set(siblings))}"
        )

    object_sha256 = {}
    downloaded_sha256 = {}
    for name, expected_file in expected_files.items():
        sibling = siblings[name]
        if sibling.size != expected_file["size"]:
            raise ValueError(f"remote size mismatch: {name}")
        remote_sha256 = lfs_sha256(sibling.lfs)
        if remote_sha256 is not None:
            if remote_sha256 != expected_file["sha256"]:
                raise ValueError(f"remote object SHA256 mismatch: {name}")
            object_sha256[name] = remote_sha256
            continue
        if name.endswith(".safetensors"):
            raise ValueError(f"remote API exposed no object SHA256 for weight file: {name}")
        local_path = Path(
            hf_hub_download(args.repo_id, name, repo_type="model", revision=info.sha, token=token, force_download=True)
        )
        digest = sha256_file(local_path)
        if digest != expected_file["sha256"]:
            raise ValueError(f"downloaded metadata SHA256 mismatch: {name}")
        downloaded_sha256[name] = digest

    report = {
        "commit": info.sha,
        "downloaded_sha256": downloaded_sha256,
        "file_count": len(siblings),
        "object_sha256": object_sha256,
        "repo_id": args.repo_id,
        "total_bytes": sum(item.size for item in siblings.values()),
        "verdict": "pass",
        "visibility": args.visibility,
    }
    write_json(args.output, report)


if __name__ == "__main__":
    main()
