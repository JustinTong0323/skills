---
name: nvfp4-conversion
description: "Convert supported Hugging Face floating-point checkpoints to NVIDIA Model Optimizer NVFP4, choosing an official whole-model PTQ/export path for dense or fitting models and a bounded routed-expert streaming path only when full-model conversion cannot fit. Includes immutable provenance, tensor-content audits, SGLang and optional MTP qualification, objective task gates, durable artifacts, and verified Hugging Face publication with disclosure-safe visibility."
---

# NVFP4 Conversion

Produce a loader-compatible NVFP4 checkpoint with evidence strong enough to distinguish a completed conversion from a process that merely exited successfully.

This skill supports two implementation paths:

1. **Whole-model PTQ/export**: use the unmodified Model Optimizer example and recipe for a supported architecture when the model and export fit the available GPU and host memory. This is the default for dense models and may also fit smaller MoE models.
2. **Bounded routed-expert streaming**: quantize disjoint fused routed-expert partitions and assemble on CPU when the whole model cannot fit and the source layout satisfies the streaming contract.

Do not select a path from a model name. Inspect the source config, safetensors metadata and optional index, Model Optimizer support, recipe scope, and measured resource headroom. Do not send a dense checkpoint through the routed-expert assembler merely because it belongs to a model family that also has MoE variants.

## Required Inputs

Obtain or discover:

- Read-only BF16 or FP16 Hugging Face source checkpoint at an immutable revision, with `config.json` and either one safetensors file or a complete safetensors index.
- Requested Model Optimizer revision, resolved once to an exact commit. The known-good reference is `87c9f8cf83021957d1a1a575c90c9a4eaaf7ef0c`; it is evidence, not a permanent default.
- An official or previously qualified Model Optimizer recipe compatible with the exact architecture and desired precision scope.
- Calibration dataset, revision, split, sample count, sequence length, batch size, seed, and algorithm.
- Target SGLang revision, hardware topology, and enough local and durable storage for source, staging, final output, and qualification artifacts.
- An authorized destination and visibility. Private or unreleased sources require a private destination.

Never place an access token in a command argument, script, log, manifest, model card, shell history, or repository file. Pass credentials through a protected secret mechanism or stdin and remove them from child environments as soon as practical.

## Read The References

Read [conversion-contract.md](references/conversion-contract.md) before selecting a path or starting conversion.

Read [online-nvfp4-serving.md](references/online-nvfp4-serving.md) before committing to offline conversion: on Blackwell serving targets, SGLang `--quantization nvfp4_online` may make a converted checkpoint unnecessary.

Read exactly one path reference after preflight:

- [whole-model-ptq.md](references/whole-model-ptq.md) for official whole-model PTQ/export.
- [routed-expert-streaming.md](references/routed-expert-streaming.md) for bounded fused routed-expert conversion and CPU assembly.

Read [validation-and-release.md](references/validation-and-release.md) before defining acceptance criteria, serving, or publishing.

## Bundled Evidence Scripts

The scripts in `scripts/` automate deterministic inspection and verification around the conversion. They deliberately do not reimplement Model Optimizer PTQ/export or provide a universal routed-expert assembler. Run them with Python 3.10 or newer; checkpoint inspection requires PyTorch and Safetensors, and remote verification requires Hugging Face Hub.

Generate source evidence and make path selection explicit. Omit `--routed-exporter-qualified` for whole-model-only work:

```bash
python scripts/preflight.py "$SOURCE" \
  --modelopt-supported yes \
  --whole-model-fit yes \
  --require-decision --output preflight.json
python scripts/inventory.py "$SOURCE" --output source-inventory.json
```

`--routed-exporter-qualified=yes` means the exact architecture, fused tensor layout, exporter, and assembler have already passed the routed contract. Fused expert keys alone are insufficient evidence.

For a routed run, also pass the exact expected layer IDs, for example `--expected-routed-layers 0,2,4`. Preflight requires the discovered fused layer set to match exactly, so non-contiguous hybrid layouts remain supported without accepting an incomplete checkpoint.

Build the immutable conversion manifest after writing normalized JSON files for calibration, environment, arguments, precision contract, and topology:

```bash
python scripts/build_manifest.py \
  --preflight preflight.json \
  --source-inventory source-inventory.json \
  --modelopt-root "$MODELOPT_ROOT" \
  --recipe "$RECIPE" \
  --calibration calibration.json \
  --environment environment.json \
  --arguments arguments.json \
  --precision-contract precision-contract.json \
  --topology topology.json \
  --artifact "$CONVERSION_RUNNER" \
  --source-repository "$SOURCE_REPOSITORY" \
  --source-revision "$SOURCE_REVISION" \
  --output conversion-manifest.json
```

After export, audit tensor structure and content, then bind candidate, durable, and hosted copies to one inventory:

```bash
python scripts/audit_checkpoint.py \
  --source "$SOURCE" \
  --output-checkpoint "$OUTPUT" \
  --output tensor-audit.json
python scripts/inventory.py "$OUTPUT" --output release-inventory.json
python scripts/inventory.py "$DURABLE_OUTPUT" --output durable-inventory.json
python scripts/compare_inventories.py release-inventory.json durable-inventory.json
printf '%s\n' "$HF_TOKEN" | python scripts/verify_hf.py \
  "$HF_REPOSITORY" release-inventory.json \
  --revision "$HF_COMMIT" --visibility private \
  --output hf-verification.json
```

If routed assembly emits precision metadata separately, pass its normalized `quantized_layers` contract with `--precision-contract`. Never weaken a failed script check without first resolving the mismatch.

## Workflow

### 1. Freeze Source And Dependency Identity

Resolve the source and Model Optimizer revisions once. Record content hashes for source config, optional index, every source weight file, calibration inputs or immutable dataset revision, conversion recipe, executable conversion artifacts, normalized arguments, and dependency environment.

Inspect config and weight metadata without loading all tensors. Resolve:

- Architecture and model class.
- Flat or nested text-config path and backbone key prefix.
- Layer types, tensor key set, source shard set, dtypes, dimensions, MTP presence, and expert layout.
- Model Optimizer architecture registration and exact recipe semantics.
- Expected output precision by module: W4A16 NVFP4, FP8, BF16/FP16, excluded, and KV-cache metadata.

Create an immutable conversion manifest before expensive work. Any input that can change output bytes belongs in its identity.

### 2. Select The Conversion Path

First confirm an offline artifact is needed at all. If the target is Blackwell serving of a BF16/FP16/FP8 MoE checkpoint on a supported FlashInfer backend and no consumer requires a serialized NVFP4 checkpoint, serve the source with `--quantization nvfp4_online` instead (see [online-nvfp4-serving.md](references/online-nvfp4-serving.md)) and skip conversion.

Choose **whole-model PTQ/export** only when all are true:

- Model Optimizer supports the exact architecture and recipe.
- The recipe's quantized and excluded scopes match the requested output.
- A dry run or defensible memory budget shows model load, calibration, export, and peak temporary allocations fit with headroom.
- The exported checkpoint format is accepted by the target runtime.

Choose **bounded routed-expert streaming** only when all are true:

- Whole-model conversion is unsupported or fails the measured resource budget.
- The checkpoint contains the supported fused routed-expert layout.
- Non-routed tensors can remain byte/logically unchanged.
- The expert partition, calibration, exporter, marker, and CPU assembler contracts are implemented and validated.

Otherwise stop as unsupported. Do not patch Model Optimizer or improvise a new quantization recipe inside a production conversion.

Record the selected path and rejected alternative in the conversion manifest.

### 3. Run A Real-Weight Preflight

For whole-model PTQ, run the smallest official real-weight calibration/export that exercises the actual architecture, recipe, tokenizer, and exporter. For routed streaming, quantize one real expert from one real layer through the production part pipeline.

Require:

- Successful real source-weight load.
- Representative calibration input, not random Gaussian placeholders.
- Finite calibration and forward values.
- Expected output tensor names, shapes, dtypes, scale forms, and quantization metadata.
- Finite, strictly positive scales.
- No unexpected quantization of protected modules such as MTP.

Do not begin the full run if preflight fails.

### 4. Convert Into Staging

Follow the selected path reference. Keep source immutable and write only to a generation-specific staging directory. Capture complete logs and exit status, but do not treat exit status as sufficient evidence.

The whole-model path exports one complete checkpoint. The routed path publishes immutable parts and markers, verifies them, then assembles one complete checkpoint on CPU. Both paths finish with an independently readable staged checkpoint and an output manifest.

### 5. Audit The Exported Checkpoint

Independently reopen config, optional index, and every weight file. Require:

1. Exact equality between the logical key map and physical tensor keys, with no duplicate JSON keys or orphan shards.
2. Tensor coverage, shapes, and dtypes derived from the actual recipe and source architecture.
3. Quantized module counts matching normalized `hf_quant_config.json` or equivalent metadata.
4. Correct NVFP4 packing/group size and finite, strictly positive scale tensors, including one F32 `input_scale` per W4A4 `NVFP4` base — its absence marks a W4A16 export regardless of labels.
5. Exact logical content equality for every tensor expected to remain unchanged, including MTP.
6. No temporary, partial, or incomplete artifact.
7. Complete source and output file inventories with content hashes.

Never infer the expected output contract from a previous model run. Derive it from the selected recipe and current source.

### 6. Qualify Runtime And Quality

Freeze task criteria before observing target results. Serve on a devbox with the target SGLang revision and explicitly record resolved server arguments, model classes, quantization mode, attention backend, linear-attention backend, page size, KV-cache dtype, graph settings, memory fraction, and OOM counters.

Keep backend namespaces distinct. A hybrid model may use `trtllm_mha` for full attention and Triton for GDN/linear attention; a Triton linear-kernel log does not prove that the full-attention backend changed.

Run:

- Health and deterministic exact-answer smoke with finite logprobs and `finish_reason=stop`.
- MTP/NEXTN smoke and acceptance evidence when the release recipe enables it.
- The complete predeclared task evaluation, gating on accuracy, stop rate, truncation, request errors, and empty generations. For reasoning models, also gate on the thinking-length distribution and one agentic long-horizon benchmark with an explicit `max_tokens`.
- Release-affecting comparisons follow the paired multi-seed statistical protocol with a predeclared non-inferiority margin; never gate on single-seed deltas.
- Final cgroup/GPU OOM audit.

Write an immutable qualification record with raw-result hashes and an explicit pass, fail, or waiver verdict.

### 7. Persist And Publish

Copy the qualified candidate to durable staging and compare complete inventories. Promote staging to the final durable path only after qualification passes.

Create the remote repository with the authorized visibility before upload. Default to private when visibility is not explicit; never make a private or unreleased source public. After upload, independently verify:

- Effective visibility equals the authorized value.
- Exact remote/local filename and byte-size sets.
- Downloaded metadata hashes.
- Remote LFS/Xet object hashes for every weight shard when exposed by the hosting API.
- Final commit identity.

Hosting services may normalize `.gitattributes`, for example by adding an LFS rule for a large tokenizer. Reconcile that exact text into local and durable copies, regenerate the final inventory, and reverify the remote revision. Do not dismiss an unexplained byte-count difference.

## Completion Report

Report:

- Source revision and content identity, Model Optimizer commit, environment, recipe, calibration, and selected path.
- Source architecture/layout and quantized, FP8, excluded, unchanged, and MTP scopes.
- Conversion and output-manifest identities, output shard/tensor/byte counts, scale audit, and unchanged-content audit.
- Runtime revision and resolved backend/topology/memory arguments.
- Exact smoke, MTP acceptance, quality, stop, truncation, error, empty-output, latency, throughput, and OOM results.
- Durable model and raw-evidence locations.
- Repository, commit, visibility, final inventory hash, metadata hashes, and weight-object hashes.
- Warnings and portability limits, including any runtime fallback or missing KV scale metadata.

Bind every result to generated manifests, reports, inventories, or logs. Do not publish a private or unreleased model identity in a public PR, example, or skill document.
