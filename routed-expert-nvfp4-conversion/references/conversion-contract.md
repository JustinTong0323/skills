# Conversion Contract

## Dependency Boundary

Use unmodified NVIDIA Model Optimizer source resolved to an exact commit. The reference known to pass the full gate chain (structural, scale, deterministic smoke with a known-good control, and task-level accuracy + stop-rate) is:

```text
87c9f8cf83021957d1a1a575c90c9a4eaaf7ef0c
```

This commit is a known-good reference, not a permanent requirement. Default to the requested current revision, including `main` or `latest`. Fetch and resolve it once when a conversion generation starts, then freeze that exact commit for the dry run and part conversion. Never let a moving ref advance inside an active generation.

Do not introduce a local Model Optimizer patch to make full-model FSDP export work. The accepted design avoids that failure mode by bounding each worker to one layer and one expert partition.

Record the exact Python, PyTorch, CUDA, Transformers, Safetensors, and Model Optimizer versions. Disable user-site packages during conversion so an undeclared package cannot change the result. Hash the normalized environment record and every pipeline artifact that can affect output.

### Refreshing A Moving Revision

Resolving a moving revision to a new commit starts a new generation. This is expected for a latest-first workflow, but it is not a resume of parts from the earlier generation. Any of these is a legitimate trigger:

- An upstream fix lands that this pipeline depends on (for example, a fix in the unified-HF NVFP4 export path, a repaired `weight_scale` layout, or tighter exclusion of internal quantizer buffers).
- An upstream streaming / disk-offload export path matures and replaces the pipeline's custom bounded exporter.
- The runtime environment moves (a newer Transformers release on which a model family stops quantizing, or a known-good container image pulls a newer Model Optimizer).

When moving:

1. Fetch the requested ref and resolve it to an exact commit.
2. Diff the new commit against the current snapshot for anything touching `modelopt/torch/export/unified_export_hf*.py`, `modelopt/torch/quantization/config.py`, the NVFP4 quantizer paths, calibration algorithms, and expert-tensor rename handlers. Treat any change in these as a behavior change, not a refactor.
3. Generate a new conversion manifest and generation directory, then rerun the full gate chain from scratch: real-weight dry run → full four-worker conversion → CPU assembly → structural and scale validation → deterministic smoke on both a known-good control and the target checkpoint → task accuracy + stop-rate.
4. Leave the earlier generation immutable. Do not reuse its parts in the new generation.

A refreshed generation that only reaches "loads without error" is not validated. NVFP4 calibration or export behavior can shift silently between adjacent commits, and the failure mode this pipeline is designed to catch is exactly "structure passes while numerics drift".

## Quantized Scope

Quantize only backbone routed experts matching the canonical suffix:

```text
layers.*.mlp.experts.*
```

Resolve the complete source prefix from the weight map. Valid checkpoints may expose the suffix below `model.layers` or below a wrapper such as `model.language_model.layers`. Never select a prefix from the model name or a fixed string offset. Require exactly one candidate prefix to contain every expected fused routed tensor, embedding, per-layer post-attention RMSNorm, and final normalization key.

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

This preprocessing is part of the conversion-manifest identity. A change to embedding selection, normalization, epsilon, seeds, batch count, sequence length, or token range invalidates all earlier parts.

Do not calibrate routed experts with random Gaussian hidden states. In a completed conversion, that input distribution underestimated real routed activation magnitude, produced incorrect W13 input scales, passed structural validation, and failed deterministic behavior.

## Source Layout

Resolve architecture fields from either the top-level config or one declared nested text config such as `text_config`. If both locations define a required field, require equal values. Record the selected config path and reject conflicting duplicates.

Create a canonical-to-source mapping for routed tensors, embeddings, layer normalization, attention, shared experts, final normalization, and LM head. Use the mapping for preflight, calibration, shard staging, assembly, and validation. Parse the layer number from the structural segments immediately preceding `.mlp.experts`; never depend on the byte length of a known prefix.

Treat the resolved config path, backbone prefix, and canonical-to-source mapping as conversion identity. A layout change requires a new manifest and invalidates resume, even when the tensor payload is otherwise equivalent.

## Conversion Generation

Resolve all conversion-affecting moving inputs before producing the dry-run part. Finalize one canonical conversion manifest containing:

- Exact source repository revision or local artifact identity and content identities for config, index, and every source file read by conversion or calibration.
- Exact Model Optimizer commit.
- Normalized dependency environment hash.
- SHA256 for the exporter, coordinator, and shared modules executed while producing routed parts.
- All routed-part-affecting command arguments, source layout, partitioning, quantization recipe, and calibration contract.

For each source file, record its relative path, size, and cryptographic content identity. A provider content object ID is acceptable only when it belongs to the exact immutable source revision and the local file has been verified against it. Otherwise compute SHA256 locally. Filename, modification time, repository name, and index membership are not content identity.

Serialize the conversion manifest with deterministic JSON rules and compute `conversion_manifest_sha256`. Store it read-only under a generation directory keyed by the full digest. The full digest is authoritative; a shortened prefix is display-only. Once a dry-run or production part exists, never edit the manifest. Any changed field creates a new generation.

This design keeps part markers small. A marker proves which immutable generation produced a part by referencing the conversion-manifest digest; it does not repeat every dependency field. Do not include the reference config, assembler, validator, runtime, or evaluation harness unless it actually executes while producing routed-part bytes.

## Conversion Manifest

Derive every architecture value from the resolved text config and the safetensors index. The manifest must contain:

```json
{
  "source_config_sha256": "<sha256>",
  "source_index_sha256": "<sha256>",
  "conversion_source_files": [
    {"name": "<relative-path>", "size": "<integer>", "content_id": {"scheme": "sha256-or-provider-oid", "value": "<digest>"}}
  ],
  "modelopt_commit": "<resolved-commit>",
  "environment_manifest_sha256": "<sha256>",
  "conversion_artifacts": [
    {"name": "<relative-path>", "sha256": "<sha256>"}
  ],
  "conversion_arguments": "<complete-normalized-object>",
  "source_layout": {
    "text_config_path": "<root-or-nested-path>",
    "backbone_prefix": "<prefix-before-layers>",
    "canonical_key_map": "<complete-normalized-object>"
  },
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
  "calibration_contract": "<complete normalized object>"
}
```

For four equal workers, require `routed_experts_per_layer % 4 == 0`. Uneven partitions are allowed only if the exporter, marker validator, and assembler all explicitly support them.

## Part Marker Identity

Each immutable part has one completion marker containing at least:

```json
{
  "conversion_manifest_sha256": "<sha256>",
  "layer": "<integer>",
  "expert_range": ["<inclusive>", "<exclusive>"],
  "tensor_entries": "<integer>",
  "output_size": "<integer>",
  "output_sha256": "<sha256>"
}
```

Write the part to a temporary file, validate it, atomically rename it to its immutable name, and only then atomically publish the marker. A marker without a matching valid part and the exact immutable conversion manifest is not complete.

## Assembly And Qualification Identity

Before assembly, resolve moving inputs such as the reference config and auxiliary metadata origin once. Freeze an assembly request containing the conversion-manifest digest, the complete source-file manifest for every tensor copied into the final checkpoint, exact reference-config identity, assembler and shared assembly-code hashes, normalized assembly environment and arguments, complete non-routed and MTP key sets, and auxiliary metadata origins and hashes. Hash it as `assembly_request_sha256`. A new assembly request may reuse verified routed parts from the referenced conversion generation.

After independently rereading the staged checkpoint, finalize an immutable assembly manifest containing `assembly_request_sha256`, the final checkpoint-payload inventory and cryptographic identities, and the per-tensor non-routed content digests. Exclude provenance records from this payload inventory so the manifest never hashes itself. Hash it as `assembly_manifest_sha256`; do not edit it after publication.

After assembly, create an immutable qualification record referencing `assembly_manifest_sha256`. Record the assembled checkpoint inventory and hashes, validator and runtime revisions, backend and topology, effective template identity, frozen acceptance criteria, test inputs, results, report hashes, waivers, and verdict. Updating validation, runtime, or evaluation tooling creates a new qualification record and reruns the affected gates; it does not alter the assembly or conversion identities.
