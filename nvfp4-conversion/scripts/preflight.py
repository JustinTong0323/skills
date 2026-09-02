#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path

from _common import (
    checkpoint_layout,
    config_value,
    is_mtp_key,
    load_json,
    mtp_layer_prefix,
    write_json,
)

ROUTED_KEY = re.compile(r"^(.*layers\.)(\d+)(\.mlp\.experts\.)(gate_up_proj|down_proj)\.weight$")
EXECUTABLE_DECISIONS = {"whole_model", "routed_expert_streaming"}


def tri_state(value: str) -> str:
    if value not in {"yes", "no", "unknown"}:
        raise argparse.ArgumentTypeError("expected yes, no, or unknown")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect a source checkpoint and select an NVFP4 conversion path from explicit evidence."
    )
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--modelopt-supported", type=tri_state, default="unknown")
    parser.add_argument("--whole-model-fit", type=tri_state, default="unknown")
    parser.add_argument("--routed-exporter-qualified", type=tri_state, default="unknown")
    parser.add_argument("--expected-routed-layers")
    parser.add_argument("--require-decision", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = load_json(args.checkpoint / "config.json")
    text_config = config.get("text_config")
    nested_quantization = text_config.get("quantization_config") if isinstance(text_config, dict) else None
    if (args.checkpoint / "hf_quant_config.json").exists() or config.get("quantization_config") or nested_quantization:
        raise ValueError("source checkpoint is already quantized")
    layout = checkpoint_layout(args.checkpoint)
    metadata = layout["tensor_metadata"]
    if args.expected_routed_layers is None:
        expected_routed_layers = None
    else:
        try:
            expected_routed_layers = {int(value) for value in args.expected_routed_layers.split(",")}
        except ValueError as error:
            raise ValueError("--expected-routed-layers must be comma-separated integers") from error
        if not expected_routed_layers or any(layer < 0 for layer in expected_routed_layers):
            raise ValueError("--expected-routed-layers must contain non-negative layer IDs")
    if args.routed_exporter_qualified == "yes" and expected_routed_layers is None:
        raise ValueError("--expected-routed-layers is required for a qualified routed exporter")
    routed = {}
    for key, item in metadata.items():
        match = ROUTED_KEY.match(key)
        if match:
            routed.setdefault((match.group(1), int(match.group(2)), match.group(3)), {})[match.group(4)] = item
    complete_routed = bool(routed) and all(
        set(projections) == {"gate_up_proj", "down_proj"} for projections in routed.values()
    )
    if complete_routed:
        prefixes = {prefix for prefix, _, _ in routed}
        layers = {layer for _, layer, _ in routed}
        shapes_compatible = all(
            len(projections["gate_up_proj"]["shape"]) == 3
            and len(projections["down_proj"]["shape"]) == 3
            and projections["gate_up_proj"]["dtype"] == "BF16"
            and projections["down_proj"]["dtype"] == "BF16"
            and projections["gate_up_proj"]["shape"][0] == projections["down_proj"]["shape"][0]
            and projections["gate_up_proj"]["shape"][1] == 2 * projections["down_proj"]["shape"][2]
            and projections["gate_up_proj"]["shape"][2] == projections["down_proj"]["shape"][1]
            for projections in routed.values()
        )
        layer_set_matches = expected_routed_layers is None or layers == expected_routed_layers
        routed_compatible = len(prefixes) == 1 and layer_set_matches and shapes_compatible
    else:
        prefixes = set()
        layers = set()
        routed_compatible = False

    if args.modelopt_supported == "yes" and args.whole_model_fit == "yes":
        decision = "whole_model"
        reason = "exact architecture/recipe support and whole-model resource fit were provided"
    elif (
        routed_compatible
        and args.routed_exporter_qualified == "yes"
        and (args.whole_model_fit == "no" or args.modelopt_supported == "no")
    ):
        decision = "routed_expert_streaming"
        reason = (
            "whole-model conversion is unavailable and a qualified exporter matches the complete fused routed layout"
        )
    elif args.modelopt_supported == "no" and (not routed_compatible or args.routed_exporter_qualified == "no"):
        decision = "unsupported"
        reason = "whole-model support is absent and no qualified compatible routed path is available"
    elif args.whole_model_fit == "no" and (not routed_compatible or args.routed_exporter_qualified == "no"):
        decision = "unsupported"
        reason = "whole-model conversion does not fit and no qualified compatible routed path is available"
    elif "unknown" in {args.modelopt_supported, args.whole_model_fit} or (
        routed_compatible and args.routed_exporter_qualified == "unknown"
    ):
        decision = "needs_evidence"
        reason = (
            "Model Optimizer support, memory fit, and any routed exporter qualification must be established explicitly"
        )
    else:
        decision = "unsupported"
        reason = "neither a supported fitting whole-model path nor the bounded fused routed layout is available"

    dtype_counts = {}
    for item in metadata.values():
        dtype_counts[item["dtype"]] = dtype_counts.get(item["dtype"], 0) + 1
    mtp_prefix = mtp_layer_prefix(config)
    mtp_keys = sorted(key for key in metadata if is_mtp_key(key, mtp_prefix))
    report = {
        "architecture": config.get("architectures", []),
        "decision": decision,
        "decision_reason": reason,
        "dtype_counts": dtype_counts,
        "hidden_layers": config_value(config, "num_hidden_layers"),
        "model_type": config.get("model_type"),
        "modelopt_supported": args.modelopt_supported,
        "mtp_layer_prefix": mtp_prefix,
        "mtp_tensor_count": len(mtp_keys),
        "routed_expert": {
            "compatible": routed_compatible,
            "exporter_qualified": args.routed_exporter_qualified,
            "expected_layers": sorted(expected_routed_layers) if expected_routed_layers is not None else None,
            "layer_count": len(layers),
            "prefixes": sorted(prefixes),
        },
        "safetensors_shard_count": len(layout["physical_files"]),
        "tensor_count": len(metadata),
        "text_model_type": (config.get("text_config") or {}).get("model_type"),
        "whole_model_fit": args.whole_model_fit,
    }
    if args.require_decision and decision not in EXECUTABLE_DECISIONS:
        # A non-executable decision is diagnostic output, not evidence: keep --output
        # untouched so the corrected rerun can write it.
        write_json(None, report)
        print(f"preflight decision is {decision}; nothing written to --output", file=sys.stderr)
        raise SystemExit(2)
    write_json(args.output, report)


if __name__ == "__main__":
    main()
