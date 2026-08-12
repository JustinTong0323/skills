#!/usr/bin/env python3
import argparse
from pathlib import Path

from _common import checkpoint_layout, directory_inventory, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a cryptographic checkpoint inventory.")
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-checkpoint-layout", action="store_true")
    args = parser.parse_args()

    report = directory_inventory(args.checkpoint)
    if not args.skip_checkpoint_layout:
        layout = checkpoint_layout(args.checkpoint)
        report.update(
            {
                "indexed_payload_bytes": layout["indexed_payload_bytes"],
                "safetensors_index": layout["index_path"].name if layout["index_path"] else None,
                "safetensors_shard_count": len(layout["physical_files"]),
                "tensor_count": len(layout["weight_map"]),
            }
        )
    write_json(args.output, report)


if __name__ == "__main__":
    main()
