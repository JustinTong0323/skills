# Conversion Contract

## Dependency Boundary

Use the unmodified NVIDIA Model Optimizer source at commit:

```text
87c9f8cf83021957d1a1a575c90c9a4eaaf7ef0c
```

Do not introduce a local Model Optimizer patch to make full-model FSDP export work. The accepted design avoids that failure mode by bounding each worker to one layer and one expert partition.

Record the exact Python, PyTorch, CUDA, Transformers, Safetensors, and Model Optimizer versions. Disable user-site packages during conversion so an undeclared package cannot change the result.

## Quantized Scope

Quantize only backbone routed experts matching:

```text
model.layers.*.mlp.experts.*
```

The target contract is:

| Component | Output |
|---|---|
| Routed expert weights | NVFP4 E2M1, packed as U8 |
| Routed expert activations | Dynamic NVFP4 E2M1 |
| Weight group size | 16 |
| Block scales | FP8 E4M3 |
| Secondary and input/global scales | FP32 |
| MTP | Source BF16 |
| Attention and linear attention | Source dtype |
| Routers and shared experts | Source dtype |
| Embeddings, normalization, LM head | Source dtype |
| KV cache | Not modified by checkpoint conversion |

Start from a deep copy of `NVFP4_EXPERTS_ONLY_CFG` and explicitly disable MTP:

```python
import copy

import modelopt.torch.quantization as mtq


def get_quant_config() -> dict:
    quant_config = copy.deepcopy(mtq.NVFP4_EXPERTS_ONLY_CFG)
    quant_config["quant_cfg"].append(
        {
            "quantizer_name": "*mtp*",
            "enable": False,
        }
    )
    return quant_config
```

Preserve the established exclusion list from the compatible reference config, then append both qualified and unqualified MTP exclusions. Do not derive the final exclusion list from a temporary slice model because it does not contain every BF16 module in the full checkpoint.

At minimum, verify exclusions cover:

```text
mtp.layers.0.mlp.experts
mtp.layers.0.mlp.experts.*
model.mtp.layers.0.mlp.experts
model.mtp.layers.0.mlp.experts.*
mtp.*
model.mtp.*
*.self_attn.*
*.linear_attn.*
*.mlp.gate*
*.mlp.shared_expert.*
*.mlp.shared_expert_gate*
model.embed_tokens
lm_head
model.norm
```

## Calibration Contract

Use four deterministic batches per layer part:

```text
batch size:       1
sequence length:  128
token IDs:        integers in [0, 128)
base seed:        1234
model seed:       base_seed + layer
batch seed:       base_seed + layer * 16 + batch
```

The token IDs index the first 128 source embedding rows. Before calibration, transform those embeddings with the model's exact post-attention RMSNorm implementation for the source layer, including its `rms_norm_eps` and any unit-offset weight semantics. Feed the resulting BF16 hidden states to the slice model.

This preprocessing is part of the marker identity. A change to embedding selection, normalization, epsilon, seeds, batch count, sequence length, or token range invalidates all earlier parts.

Do not calibrate routed experts with random Gaussian hidden states. In a completed conversion, that input distribution underestimated real routed activation magnitude, produced incorrect W13 input scales, passed structural validation, and failed deterministic behavior.

## Source Manifest

Derive every architecture value from `config.json` and the safetensors index. The manifest must contain:

```json
{
  "source_config_sha256": "<sha256>",
  "source_index_sha256": "<sha256>",
  "reference_config_sha256": "<sha256>",
  "modelopt_commit": "87c9f8cf83021957d1a1a575c90c9a4eaaf7ef0c",
  "hidden_layers": "<integer>",
  "routed_experts_per_layer": "<integer>",
  "experts_per_token": "<integer>",
  "hidden_size": "<integer>",
  "moe_intermediate_size": "<integer>",
  "worker_parts": 4,
  "expert_ranges": [
    {"part": 0, "start": "<inclusive>", "end": "<exclusive>"}
  ],
  "fused_routed_tensors": [
    {"name": "<key>", "shape": ["<dims>"], "dtype": "BF16", "shard": "<file>"}
  ],
  "non_routed_bf16_keys": ["<key>"],
  "mtp_bf16_keys": ["<key>"],
  "calibration_contract": "<complete normalized object>"
}
```

For four equal workers, require `routed_experts_per_layer % 4 == 0`. Uneven partitions are allowed only if the exporter, marker validator, and assembler all explicitly support them.

## Part Marker Identity

Each immutable part has one completion marker containing at least:

```json
{
  "source_config_sha256": "<sha256>",
  "source_index_sha256": "<sha256>",
  "reference_config_sha256": "<sha256>",
  "modelopt_commit": "<sha>",
  "layer": "<integer>",
  "expert_range": ["<inclusive>", "<exclusive>"],
  "quantization_recipe": "<complete normalized object>",
  "calibration_contract": "<complete normalized object>",
  "tensor_entries": "<integer>",
  "output_size": "<integer>",
  "output_sha256": "<sha256>"
}
```

Write the part to a temporary file, validate it, atomically rename it to its immutable name, and only then atomically publish the marker. A marker without a matching valid part is not complete.
