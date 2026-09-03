# Routed-Expert Streaming And Assembly

Use this path only when whole-model conversion does not fit or is unsupported, and the source contains supported fused routed experts. Quantize one model layer and one expert partition per worker, then assemble all unchanged tensors on CPU.

## Supported Layout

Resolve the complete source prefix for fused tensors matching the canonical suffixes:

```text
layers.*.mlp.experts.gate_up_proj
layers.*.mlp.experts.down_proj
```

Require shapes:

```text
gate_up_proj: [num_experts, 2 * moe_intermediate_size, hidden_size]
down_proj:    [num_experts, hidden_size, moe_intermediate_size]
```

Require BF16 source routed tensors and one unambiguous key mapping. Reject dense MLP projections, unfused per-expert layouts, or another MoE representation unless the exporter and assembler explicitly implement it.

## Routed Precision Contract

The established routed-only contract is:

| Component | Output |
|---|---|
| Routed expert weights | NVFP4 E2M1, packed U8 |
| Routed expert activations | Dynamic NVFP4 E2M1 |
| Weight group size | 16 |
| Block scales | FP8 E4M3 |
| Secondary/input scales | FP32 |
| MTP | Source BF16 and content-identical |
| Attention/linear attention | Source dtype and content-identical |
| Routers/shared experts | Source dtype and content-identical |
| Embeddings/norm/LM head | Source dtype and content-identical |

Start from a deep copy of the routed-experts recipe and add qualified and unqualified MTP exclusions. Preserve the compatible reference exclusion list; a slice model cannot reveal every protected full-checkpoint module.

## Calibration

For each layer part, use deterministic real-source activations. One qualified Qwen-compatible recipe used four batches, batch size 1, sequence length 128, base seed 1234, and token IDs `[0, 128)` indexing source embedding rows followed by the exact source layer post-attention RMSNorm.

Treat embedding selection, normalization semantics/epsilon, seeds, batch count, sequence length, and token range as conversion identity. Re-derive and validate representative activation ranges for a different model family. Never use random Gaussian hidden states as production calibration.

## Real-Weight Dry Run

Run exactly one real layer and one real expert through the production load, calibration, quantization, export, rename, tensor validation, and marker code. Require finite forward output, expected tensor forms, and finite positive scales before starting workers.

## Bounded Workers

Use independent single-GPU processes without distributed collectives. Partition experts into disjoint ranges. Uneven ranges are allowed only when exporter, marker validator, and assembler all support them.

For every layer, a worker must:

1. Open only required source shards and slice its assigned experts on CPU.
2. Construct one layer containing only that expert range.
3. Move only its weights and calibration state to one GPU.
4. Apply the routed-only NVFP4 recipe with MTP disabled.
5. Calibrate deterministically and require finite output.
6. Export to a unique temporary directory.
7. Restore global layer/expert names.
8. Validate tensor coverage, shapes, dtypes, and positive scales.
9. Atomically publish the part, then its completion marker.
10. Release all model, calibration, export, and source-slice references; collect garbage and clear the CUDA allocator before the next layer.

GPU growth across layers is a lifecycle defect, not a reason to add ranks.

## Part Identity And Resume

Each marker contains conversion-manifest digest, layer, expert range, tensor count, output size, and output SHA256. A marker is complete only after the part is validated and atomically renamed.

Resume one explicitly selected immutable generation. Never resolve a moving ref during resume and never scan other generations for reusable parts. Reuse only when marker identity, size, and SHA256 match; quarantine partial or mismatched files.

Supervise worker exit codes, last valid marker, GPU/host memory, cgroup OOM counters, filesystem capacity, part/marker counts, and latest verified completion. A live PID or growing log is not proof of progress.

## CPU Assembly

Freeze a separate assembly request containing conversion digest, source and reference-config identities, assembler/environment/argument hashes, expected routed/non-routed/MTP key sets, and auxiliary metadata inventory.

The assembler must:

1. Re-hash every part and marker and reject missing, duplicate, overlapping, or out-of-range experts.
2. Replace original fused routed tensors only after every replacement is verified.
3. Copy every other source tensor unchanged and record canonical logical content digests.
4. Preserve MTP BF16 and its complete exclusions.
5. Copy behavior-bearing metadata from one declared immutable origin.
6. Generate complete config, quantization config, and safetensors index.
7. Remove stale KV-cache quantization declarations unless the selected recipe explicitly owns them.
8. Write bounded shards through temporary files, validate, fsync, and atomically rename.
9. Independently reread the staged output and finalize an output manifest.

Do not assemble directly into a final path.

## Metadata And Multi-Node Notes

Tokenizer, processor, generation config, standalone/embedded chat templates, remote code, and model config are behavior-bearing. Hash their origin and parse-render supported text/tool/thinking modes. Align metadata only as a separate verified revision when weights remain unchanged.

For multi-node runtime validation, verify per-node rank count rather than letting global TP overwrite `LOCAL_SIZE`, bound prefetch threads to page-cache headroom, allow for real rank load imbalance, and prove every rank loaded its shard set. Rank-0 health is insufficient.

## Failure Modes

| Symptom | Required response |
|---|---|
| Full-model export OOM | Use this path only after fused-layout compatibility is proven |
| Memory grows per layer | Release retained slice/export objects before continuing |
| Resume skips stale output | Resume by full immutable digest and re-hash every part |
| Missing/duplicate experts | Reject assembly and inspect range/global renaming |
| MTP becomes NVFP4 | Correct exclusions and create a new generation |
| Non-routed metadata matches but values differ | Fail independent content audit |
| Prefix keys are missing | Rediscover nested text config/backbone prefix; reject fixed offsets |
| Multi-node load hangs | Verify per-rank shard coverage, local/global rank sizing, and page-cache pressure |
