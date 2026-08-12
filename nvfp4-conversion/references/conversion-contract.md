# Conversion Contract

## Dependency Boundary

Use unmodified NVIDIA Model Optimizer source resolved to an exact commit. A commit known to have passed whole-model and routed-expert qualification on Blackwell-class hardware is:

```text
87c9f8cf83021957d1a1a575c90c9a4eaaf7ef0c
```

Treat this as a reference snapshot, not a promise for every architecture. Resolve a requested moving ref once at the start of a fresh generation. Never let it advance during conversion or resume.

Record exact Python, PyTorch, CUDA, Transformers, Datasets, Safetensors, Hugging Face Hub, and Model Optimizer versions; GPU family/count; conversion image; normalized environment; executable artifact hashes; and all output-affecting arguments. Disable undeclared user-site packages.

A Model Optimizer or environment change starts a new conversion generation. Diff changes affecting unified HF export, quantization configuration, NVFP4 quantizers, calibration, architecture adapters, and tensor-name translation, then rerun the full gate chain. A successful load on a new commit is not numeric qualification.

## Immutable Source

Pin the source repository to an immutable revision. Before conversion, record relative name, size, and SHA256 or an equally strong verified provider object identity for:

- `config.json`, every safetensors file, and the safetensors index when present.
- Every source shard referenced by the index, or the single source safetensors file.
- Tokenizer, processor, generation config, standalone chat template, and remote-code files used by conversion or copied to output.
- Any external reference config.

Repository name, revision string, filename, modification time, and index membership are not enough to detect local corruption. Recheck source content identity before resume, assembly, and final audit.

## Architecture And Layout Discovery

Resolve fields from top-level or one declared nested text config such as `text_config`. If both define a required field, require equality. Discover the complete backbone prefix from the weight map and require it to cover every expected layer and calibration tensor.

Record:

- Architecture/model class and model type.
- Hidden layers, hidden/intermediate dimensions, attention layout, layer types, vocabulary, and context length.
- Routed/shared expert counts and fused layout when present.
- MTP layer count and complete MTP key set.
- Source key-to-file map, physical key set, shapes, and dtypes.
- Canonical-to-source key mapping for all modules touched by conversion or validation.

Reject ambiguous prefixes, conflicting configs, missing keys, duplicate JSON keys, prequantized inputs, unsupported source dtypes, or an index whose logical keys differ from physical shard keys. Treat a single safetensors file as an implicit one-file key map and still inventory every physical key.

## Path Selection

Record `conversion_path` as `whole_model` or `routed_expert_streaming`.

### Whole Model

Require an official Model Optimizer architecture adapter and recipe for the exact checkpoint structure. Inspect the recipe, not only its name. Normalize its quantization map into module patterns with expected algorithm, bit width, group size, output tensor forms, and exclusions.

Build a resource budget using source indexed tensor bytes, expected model dtype in memory, calibration activations, exporter temporary storage, device placement, and prior measured peak when available. A model that fits as checkpoint files may still OOM during graph construction, calibration, or export.

### Routed Expert Streaming

Use only for the fused layout and lifecycle in [routed-expert-streaming.md](routed-expert-streaming.md). Whole-model OOM alone does not make an arbitrary architecture compatible with that assembler.

Record why whole-model conversion was rejected and prove that all non-routed tensors can remain logically identical.

## Recipe And Precision Contract

Copy the chosen recipe before adding exclusions. Never mutate a shared global recipe object. Record the canonical recipe representation and SHA256.

The precision contract must enumerate every module base and one of:

- `W4A16_NVFP4`, including group size and output packed/scale dtypes.
- `FP8`, including expected weight and scale forms.
- Source dtype, content unchanged.
- Excluded from quantization for a documented reason.

Treat KV-cache quantization separately from weight conversion. A recipe name ending in `kv_fp8_cast` may write only metadata and no per-layer KV scale tensors. Audit what was exported, then record the runtime behavior. Do not claim calibrated KV scales when the runtime reports fallback scales of `1.0`.

MTP policy must be explicit. If excluded, require source-identical content. If quantized, require a dedicated supported recipe and independent runtime qualification; never let MTP be quantized by an accidental wildcard.

## Calibration Contract

Freeze before conversion:

- Dataset repository/revision/config/split or local content identity.
- Sample selection/order, sample count, tokenizer revision, sequence length, truncation/padding, batch size, seed, and calibration algorithm.
- Prompt/text extraction and preprocessing code identity.
- Architecture-specific forward kwargs.

Use production-representative text or activation data. Random Gaussian calibration is not a substitute. For a new model family, first compare calibration activation ranges with representative real traffic.

A production whole-model reference that passed one dense hybrid Qwen-family conversion used 1,024 `cnn_dailymail` train samples, sequence length 512, batch size 1, seed 1234, and max calibration. This is evidence for that recipe and family, not a universal default.

The routed path's layer/expert calibration is defined separately because it cannot run the full model.

## Conversion Manifest

Canonicalize deterministic JSON and hash it before expensive work. Include at least:

```json
{
  "source": {
    "repository": "<repo-or-local-id>",
    "revision": "<immutable-revision>",
    "files": [{"name": "<path>", "size": 0, "sha256": "<sha256>"}]
  },
  "architecture": {
    "model_class": "<class>",
    "text_config_path": "<root-or-path>",
    "backbone_prefix": "<prefix>",
    "normalized_layout": "<object>"
  },
  "conversion_path": "<whole_model-or-routed_expert_streaming>",
  "path_decision": "<normalized-object>",
  "modelopt_commit": "<commit>",
  "environment_sha256": "<sha256>",
  "conversion_artifacts": [{"name": "<path>", "sha256": "<sha256>"}],
  "arguments": "<normalized-object>",
  "recipe": "<name-or-path>",
  "recipe_sha256": "<sha256>",
  "precision_contract": "<normalized-object>",
  "calibration": "<normalized-object>",
  "topology": "<normalized-object>"
}
```

Store the manifest under a generation directory keyed by the full digest. Once conversion output exists, never edit it. Any output-affecting change creates a new generation.

## Output Manifest

After export or assembly, independently reread the candidate and record:

- Config and quantization-config hashes.
- Exact file inventory with size and SHA256.
- Indexed payload bytes, tensor count, shard count, index hash, and physical/index key equality.
- Counts by precision contract and output dtype.
- Scale value count, minimum, maximum, finiteness, and positivity.
- Complete unchanged-key set and canonical content equality result.
- MTP key count, dtype, and content equality result.
- Conversion-manifest digest and conversion log digest.

Exclude self-referential provenance files from any payload hash that would otherwise recurse. Add final provenance and qualification files, then generate a separate release inventory.

## Qualification Identity

Freeze acceptance criteria before target evaluation. A qualification record references one output manifest and records validator, SGLang, kernels, backend, topology, effective template, dataset/harness identities, test inputs, raw-result hashes, OOM counters, warnings, waivers, and verdict.

Updating validation or runtime tooling creates a new qualification record. It does not mutate conversion or output identity.
