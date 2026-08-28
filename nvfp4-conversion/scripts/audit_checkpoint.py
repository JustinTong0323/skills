#!/usr/bin/env python3
import argparse
import re
from collections import Counter
from pathlib import Path
from typing import Any

from _common import checkpoint_layout, compare_tensor_content, load_json, scan_positive_finite, write_json

NVFP4_GROUP_SIZE = 16
PROTECTED_BASE = re.compile(r"(^|\.)(mtp|gate|router)(\.|$)")


def mtp_layer_prefix(config: dict) -> str | None:
    # HF GLM names the nextn layer model.layers.<num_hidden_layers>.* with no "mtp" substring
    layers = config.get("num_hidden_layers")
    if isinstance(layers, int) and config.get("num_nextn_predict_layers"):
        return f"model.layers.{layers}."
    return None


def quantized_layers(output: Path, contract: Path | None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if contract:
        data = load_json(contract)
    else:
        path = output / "hf_quant_config.json"
        if not path.exists():
            raise ValueError("hf_quant_config.json or --precision-contract is required")
        data = load_json(path)
    quantization = data.get("quantization", data)
    layers = quantization.get("quantized_layers")
    if not isinstance(layers, dict) or not layers:
        raise ValueError("a non-empty quantized_layers map is required; routed assembly must provide --precision-contract")
    return layers, quantization


def expected_forms(algorithm: str) -> dict[str, str]:
    if algorithm == "NVFP4":
        return {"weight": "U8", "weight_scale": "F8_E4M3", "weight_scale_2": "F32", "input_scale": "F32"}
    if algorithm == "W4A16_NVFP4":
        return {"weight": "U8", "weight_scale": "F8_E4M3", "weight_scale_2": "F32"}
    if algorithm == "FP8":
        return {"weight": "F8_E4M3", "weight_scale": "F32", "input_scale": "F32"}
    raise ValueError(f"unsupported quantization algorithm in audit: {algorithm}")


def validate_shape(
    base: str,
    algorithm: str,
    group_size: int | None,
    source_shape: list[int],
    form: str,
    output_shape: list[int],
) -> None:
    if algorithm in ("NVFP4", "W4A16_NVFP4"):
        if len(source_shape) < 2 or source_shape[-1] % 2:
            raise ValueError(f"NVFP4 source weight must have an even innermost input width: {base}")
        if group_size != NVFP4_GROUP_SIZE or source_shape[-1] % group_size:
            raise ValueError(f"invalid NVFP4 group_size for {base}: {group_size}")
        expected = {
            "weight": source_shape[:-1] + [source_shape[-1] // 2],
            "weight_scale": source_shape[:-1] + [source_shape[-1] // group_size],
            "weight_scale_2": [],
            "input_scale": [],
        }[form]
    else:
        expected = source_shape if form == "weight" else []
    if output_shape != expected:
        raise ValueError(f"shape mismatch for {base}.{form}: expected={expected}, actual={output_shape}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit a unified-HF ModelOpt NVFP4 checkpoint against its floating-point source.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-checkpoint", type=Path, required=True)
    parser.add_argument("--precision-contract", type=Path)
    parser.add_argument("--rows-per-chunk", type=int, default=1024)
    parser.add_argument("--allow-quantized-protected", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.rows_per_chunk <= 0:
        raise ValueError("--rows-per-chunk must be positive")

    source_config = load_json(args.source / "config.json")
    output_config = load_json(args.output_checkpoint / "config.json")
    source = checkpoint_layout(args.source)
    output = checkpoint_layout(args.output_checkpoint)
    layer_map, quantization = quantized_layers(args.output_checkpoint, args.precision_contract)
    config_quantization = output_config.get("quantization_config")
    if not isinstance(config_quantization, dict):
        raise ValueError("output config.json has no quantization_config object")
    config_layers = config_quantization.get("quantized_layers")
    if config_layers is not None and config_layers != layer_map:
        raise ValueError("config.json and precision contract disagree on quantized_layers")
    contract_algorithm = quantization.get("quant_algo")
    if not isinstance(contract_algorithm, str) or not contract_algorithm:
        raise ValueError("precision contract has no quant_algo")
    config_algorithm = config_quantization.get("quant_algo")
    if config_algorithm is not None and config_algorithm != contract_algorithm:
        raise ValueError("config.json and precision contract disagree on quant_algo")
    kv_cache_algorithm = quantization.get("kv_cache_quant_algo")
    kv_cache_scheme = config_quantization.get("kv_cache_scheme")
    if (kv_cache_algorithm == "FP8") != (kv_cache_scheme is not None):
        raise ValueError("config.json and precision contract disagree on the FP8 KV-cache declaration")
    if kv_cache_algorithm == "FP8" and kv_cache_scheme != {"dynamic": False, "num_bits": 8, "type": "float"}:
        raise ValueError("config.json kv_cache_scheme does not represent the FP8 precision contract")
    mtp_prefix = mtp_layer_prefix(source_config)
    protected = sorted(
        base
        for base in layer_map
        if PROTECTED_BASE.search(base) or (mtp_prefix and base.startswith(mtp_prefix))
    )
    if protected and not args.allow_quantized_protected:
        raise ValueError(
            "protected modules appear in quantized_layers; pass --allow-quantized-protected only with a "
            f"dedicated recipe and independent qualification: {protected[:10]}"
        )
    # Exported attention k/v bmm scales silently corrupt MLA serving in sglang;
    # official NVFP4 checkpoints ship none, so treat their presence as a violation.
    kv_scale_leaked = sorted(key for key in output["tensor_metadata"] if key.endswith(".k_scale") or key.endswith(".v_scale"))
    if kv_scale_leaked:
        raise ValueError(f"KV bmm scale tensors must not ship in the checkpoint: {kv_scale_leaked[:10]}")

    source_meta = source["tensor_metadata"]
    output_meta = output["tensor_metadata"]
    transformed_source_keys = set()
    expected_output_keys = set(source_meta)
    algorithm_counts = Counter()
    scale_keys = []

    for base, spec in layer_map.items():
        algorithm = spec.get("quant_algo")
        forms = expected_forms(algorithm)
        source_key = base + ".weight"
        if source_key not in source_meta:
            raise ValueError(f"quantized base has no source weight: {base}")
        transformed_source_keys.add(source_key)
        algorithm_counts[algorithm] += 1
        expected_output_keys.update(base + "." + form for form in forms)
        for form, dtype in forms.items():
            key = base + "." + form
            if key not in output_meta:
                raise ValueError(f"missing quantized tensor: {key}")
            if output_meta[key]["dtype"] != dtype:
                raise ValueError(f"dtype mismatch for {key}: expected={dtype}, actual={output_meta[key]['dtype']}")
            validate_shape(
                base,
                algorithm,
                spec.get("group_size"),
                source_meta[source_key]["shape"],
                form,
                output_meta[key]["shape"],
            )
            if "scale" in form:
                scale_keys.append(key)

    if set(output_meta) != expected_output_keys:
        raise ValueError(
            f"output key mismatch: missing={sorted(expected_output_keys - set(output_meta))[:10]} "
            f"extra={sorted(set(output_meta) - expected_output_keys)[:10]}"
        )

    unchanged_keys = sorted(set(source_meta) - transformed_source_keys)
    mtp_keys = [
        key
        for key in unchanged_keys
        if key.startswith("mtp.") or ".mtp." in key or (mtp_prefix and key.startswith(mtp_prefix))
    ]
    unchanged_bytes = 0
    for key in unchanged_keys:
        if output_meta[key] != source_meta[key]:
            raise ValueError(f"unchanged tensor metadata mismatch: {key}")
        compare_tensor_content(
            args.source,
            source["weight_map"][key],
            args.output_checkpoint,
            output["weight_map"][key],
            key,
            args.rows_per_chunk,
        )
        element_count = 1
        for dimension in source_meta[key]["shape"]:
            element_count *= dimension
        dtype_size = {"BF16": 2, "F16": 2, "F32": 4, "F64": 8, "I64": 8, "I32": 4, "U8": 1}.get(
            source_meta[key]["dtype"]
        )
        if dtype_size:
            unchanged_bytes += element_count * dtype_size

    scale_count = 0
    scale_minimum = float("inf")
    scale_maximum = float("-inf")
    for key in sorted(scale_keys):
        count, minimum, maximum = scan_positive_finite(
            args.output_checkpoint,
            output["weight_map"][key],
            key,
            args.rows_per_chunk,
        )
        scale_count += count
        scale_minimum = min(scale_minimum, minimum)
        scale_maximum = max(scale_maximum, maximum)

    report = {
        "algorithm_base_counts": dict(sorted(algorithm_counts.items())),
        "kv_cache_quant_algo": kv_cache_algorithm,
        "mtp_tensor_count": len(mtp_keys),
        "output_indexed_payload_bytes": output["indexed_payload_bytes"],
        "output_shard_count": len(output["physical_files"]),
        "output_tensor_count": len(output_meta),
        "quantized_layers_recorded_in_config": config_layers is not None,
        "quantized_protected_bases": protected,
        "scale_max": scale_maximum,
        "scale_min": scale_minimum,
        "scale_tensor_count": len(scale_keys),
        "scale_value_count": scale_count,
        "unchanged_bytes": unchanged_bytes,
        "unchanged_tensor_count": len(unchanged_keys),
        "verdict": "pass",
    }
    write_json(args.output, report)


if __name__ == "__main__":
    main()
