---
name: routed-expert-nvfp4-conversion
description: "Convert a very large Qwen-compatible fused-MoE BF16 checkpoint into a routed-expert-only NVIDIA Model Optimizer NVFP4 checkpoint with four bounded single-GPU workers, resumable immutable parts, CPU assembly, structural and scale validation, SGLang smoke tests, and private Hugging Face publication. Use when full-model PTQ or export exceeds GPU memory, when MTP and non-routed modules must remain BF16, or when an existing streaming conversion must be resumed, audited, validated, or published."
---

# Routed-Expert NVFP4 Conversion

Produce a loader-compatible checkpoint without ever materializing the full model on one GPU. Quantize one model layer and one expert partition per worker, export immutable parts, assemble all non-routed tensors on CPU, and release only after format and behavior gates pass.

This workflow targets Qwen-compatible checkpoints whose routed experts are stored as fused `gate_up_proj` and `down_proj` tensors. Discover dimensions, the text-config location, and the backbone key prefix from source metadata. Do not copy architecture constants, paths, credentials, scheduler names, or private model identities from an earlier run.

## Required Inputs

Obtain or discover:

- Read-only unified Hugging Face BF16 checkpoint with `config.json` and `model.safetensors.index.json`.
- Compatible Model Optimizer quantized config whose established exclusion list must be preserved.
- NVIDIA Model Optimizer checkout at a requested revision. Resolve `main`, `latest`, or another moving ref to one exact commit when a conversion generation starts. The known-good reference is `87c9f8cf83021957d1a1a575c90c9a4eaaf7ef0c` (see [conversion-contract.md](references/conversion-contract.md#dependency-boundary)).
- Four GPUs visible to four independent single-GPU workers.
- Staging and final output directories on the same filesystem.
- Enough storage for source staging, immutable routed parts, BF16 assembly shards, and validation reads.
- Target SGLang revision and hardware capable of ModelOpt FP4 inference.

Record source and dependency identities before conversion. Never place an access token in a command, log, manifest, model card, or repository file.

## Read The References

Read [conversion-contract.md](references/conversion-contract.md) before implementing or launching any worker.

Read [streaming-and-assembly.md](references/streaming-and-assembly.md) before the dry run, full conversion, resume, or assembly stage.

Read [validation-and-release.md](references/validation-and-release.md) before declaring success, launching SGLang, or publishing the result.

## Workflow

### 1. Create A Conversion Manifest

Inspect metadata without loading the full model. Record:

- SHA256 of source `config.json` and source index, plus content identities for every source file read by conversion.
- Resolved flat or nested text-config path and the discovered backbone key prefix.
- Layer count, routed expert count, experts selected per token, hidden size, and MoE intermediate size.
- Exact fused routed tensor names, shapes, dtypes, and source shards.
- Worker count and disjoint expert ranges.
- Exact Model Optimizer commit, conversion environment, conversion-code hashes, output-affecting conversion arguments, recipe, and calibration contract.

Canonicalize this information into an immutable conversion manifest, hash it, and create a generation directory keyed by the full `conversion_manifest_sha256`. Only inputs that can change routed-part bytes belong in this identity. Abort on an ambiguous backbone prefix, inconsistent flat and nested text configs, a missing key, an unexpected source dtype, an unsupported fused layout, or expert counts that cannot be partitioned safely.

### 2. Run A Real-Weight Dry Run

Quantize one real expert from one real layer before scheduling the full conversion. The dry run must:

- Slice BF16 weights from safetensors on CPU.
- Build representative calibration hidden states from source embedding rows and the source layer's post-attention RMSNorm.
- Insert the expected Model Optimizer quantizers.
- Complete deterministic calibration and a finite forward pass.
- Export compressed weights successfully.
- Match the expected tensor names, shapes, and dtypes.
- Produce finite, strictly positive scales.

Do not proceed if any gate fails. Random Gaussian hidden states are not a valid substitute for representative routed-MoE inputs; they can produce a structurally valid checkpoint with badly saturated activation scales. The concrete calibration recipe in the reference contract (deterministic token IDs indexing source embedding rows, then the source layer's post-attention RMSNorm) was validated end-to-end on the reference model family. When targeting a different model family, re-derive and re-validate that the chosen token window is representative of real routed-MoE input magnitudes before trusting it.

### 3. Run Four Independent Workers

Use four processes with one GPU each and no distributed collectives. For each layer, every worker handles one disjoint expert range and performs the bounded lifecycle defined in [streaming-and-assembly.md](references/streaming-and-assembly.md).

At the end of every part, atomically publish the safetensors file followed by a completion marker containing the conversion-manifest digest and part identity. Release all model, calibration, export, and source-slice references before advancing to the next layer.

Supervise progress using marker validation, worker exit status, GPU memory, host memory, disk capacity, and OOM counters. A live process or growing log alone is not proof of progress.

### 4. Resume By Identity

Resume only an explicitly selected generation. Reuse a part only when its marker references the exact conversion-manifest digest and its output size and SHA256 match. A fresh start resolves moving refs again; it creates a new generation only when a conversion-affecting identity changes. Assembler, validator, runtime, or evaluation changes do not invalidate routed parts. Quarantine incomplete artifacts within the selected generation and regenerate them.

### 5. Assemble On CPU

Verify the immutable conversion manifest, every part, and every marker before copying source data. Resolve any moving assembly inputs once and freeze an assembly request containing the conversion digest, complete source and reference-config identities, assembler identity, assembly arguments, and auxiliary metadata inventory. Replace only the original fused routed expert tensors. Copy all remaining source tensors unchanged and record canonical per-tensor content digests, preserve MTP in BF16, merge the reference exclusion list, remove KV-cache quantization declarations, and generate complete configs and index files. Copy auxiliary metadata from one declared source, hash it, and reject unresolved standalone-versus-embedded chat-template conflicts. After independent reread validation, finalize an immutable assembly manifest containing the request digest and output identities.

Build in staging, run structural checks there, then publish with a same-filesystem atomic rename. Never assemble directly into the final path.

### 6. Validate And Qualify

Run all gates in [validation-and-release.md](references/validation-and-release.md):

1. Full structural coverage and unique index entries.
2. Exact routed tensor shape and dtype contract.
3. Exact per-key content equality for every non-routed tensor, including MTP.
4. Finite, strictly positive scale scan.
5. SGLang load, backend initialization, health, and template-compatible deterministic finite-output smoke.
6. MTP initialization and acceptance evidence when the release recipe enables MTP.
7. Stop-rate and task-accuracy evaluation against criteria frozen before the run, unless the user explicitly waives it. A waiver is itself part of the release decision and must be recorded in the completion report (see [validation-and-release.md](references/validation-and-release.md#task-evaluation)).

A successful load is not a successful conversion. A finite but wrong or non-terminating response also fails the behavioral gate.

Write an immutable qualification record that references the assembly manifest and records validator, runtime, template, acceptance-criteria, result, and report identities. Updating qualification tooling reruns the applicable gates without invalidating conversion parts or assembly output.

### 7. Publish Privately

Confirm repository ownership, private visibility, organization policy, MFA/token permissions, and available storage before transfer. Include a version-bearing checkpoint name that binds the source revision or tag, Model Optimizer revision, scope, and calibration generation.

After upload, compare the complete remote filename and size inventory with the local publication and verify hashes for every metadata file. Delete another copy only after the intended private repository passes this independent verification and the user has authorized deletion.

## Completion Report

Report:

- Conversion-generation ID, conversion-manifest hash, assembly-manifest hash, qualification-record hash, source content identity, and exact Model Optimizer identity.
- Resolved source layout and auxiliary metadata origins and hashes.
- Quantized and BF16 scopes.
- Calibration contract and partitioning.
- Part/marker, shard, tensor, and dtype counts derived from the manifest.
- Structural and scale validator results.
- Runtime topology, backend selection, health result, output, finish reason, and finite-logprob count.
- Chat-template hash, template kwargs, and, when enabled, MTP initialization and acceptance evidence.
- Predeclared quality criteria and accuracy, stop-rate, truncation, and error results. If the user waived a dataset, record the dataset, the scope of the completed smaller result, the waiver itself, and who authorized it — do not let a waived line read as "passed".
- Final private repository, revision, visibility, inventory, and metadata hashes.
- Any remaining runtime, quality, or portability risks.

Never report success from expected constants alone. Bind every numeric result to generated manifests, indexes, reports, or logs.
