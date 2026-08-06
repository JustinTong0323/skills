# Validation And Release

## Structural Validation

Parse JSON with duplicate-key rejection. For every indexed shard, require that the physical safetensors key set exactly equals the keys assigned to that shard.

Derive expected counts from the manifest:

```text
source routed tensors = layers * 2 fused projections
output routed tensors = layers * experts * 3 projections * 4 tensor forms
scale tensors          = layers * experts * 3 projections * 3 scale forms
non-routed tensors     = source keys - source routed keys
```

For each output routed key, verify:

- Layer and expert are in range.
- Projection is `gate_proj`, `up_proj`, or `down_proj`.
- Tensor form is `weight`, `weight_scale`, `weight_scale_2`, or `input_scale`.
- Packed weights are U8.
- Block scales are FP8 E4M3.
- Secondary and input scales are FP32.
- Shapes agree with hidden size, intermediate size, packing, and group size.

Require output non-routed keys to equal the complete source non-routed set. Compare each non-routed tensor's shape and dtype with source metadata. Require every MTP tensor to remain present and BF16.

Validate `config.json` and `hf_quant_config.json`:

- `quant_method` identifies Model Optimizer.
- `quant_algo` is `NVFP4`.
- Weight and input activation settings specify 4 bits and group size 16.
- The merged ignore list includes all protected BF16 modules and MTP patterns.
- No KV-cache quantization field is present.

## Scale Validation

Scan every routed `weight_scale`, `weight_scale_2`, and `input_scale` tensor. Require every value to be finite and strictly positive. Record the checked count and global minimum and maximum.

Parallelize by shard with bounded CPU workers. Fail on the first missing shard, index mismatch, non-finite value, zero, or negative scale. Never assemble around a bad part.

## SGLang Runtime Gate

Use the target SGLang revision and explicitly select ModelOpt FP4 runtime support:

```bash
python3 -m sglang.launch_server \
  --model-path "$OUTPUT_CHECKPOINT" \
  --served-model-name nvfp4-smoke \
  --trust-remote-code \
  --quantization modelopt_fp4 \
  --fp4-gemm-backend flashinfer_cutlass \
  --moe-runner-backend flashinfer_trtllm \
  --tensor-parallel-size <tp>
```

`flashinfer_cutlass` and `flashinfer_trtllm` are example values known to work on Blackwell-class hardware with recent FlashInfer. They are not portable defaults — Hopper-class GPUs, older FlashInfer revisions, or model families without a tuned TRT-LLM MoE path need a different combination. Select these backends against your own hardware and FlashInfer version, not by copying from another cluster.

Use topology, attention backend, memory, offload, graph, and distributed flags appropriate for the hardware and SGLang revision. Do not carry flags from another cluster without validating them.

Before sending a request, require:

- Every shard loaded.
- The expected model class initialized.
- Runtime reports `quantization=modelopt_fp4` and `quant_algo=NVFP4`.
- Requested FP4 GEMM and routed-MoE backends initialized.
- Warmup and CUDA Graph capture completed, or graph capture was explicitly disabled.
- `/health` returns success.
- GPU and cgroup OOM counters remain unchanged.

Send a deterministic request with thinking disabled, temperature 0, bounded `max_tokens`, and token logprobs. Require the expected answer, `finish_reason=stop`, at least one token logprob, and all logprobs finite. Save the raw response before enforcing assertions so failures remain diagnosable.

Finite output alone is insufficient. Repetition, a wrong answer, `finish_reason=length`, or missing EOS fails the behavioral gate.

## Runtime Control Test

When a structurally valid checkpoint loads but behaves incorrectly, run the identical launch and request against a known-good public ModelOpt NVFP4 checkpoint from the same model family. Hold topology, kernels, offload, prompt, and sampling constant.

If both checkpoints fail only with CPU offload enabled, investigate registered parameter aliases and stale tensor views before blaming conversion. One observed failure came from an unregistered view retaining pre-load GPU storage after the registered parameter was offloaded and replaced. The control passed again when the runtime kept the alias registered and derived the required view at use time.

Do not waive the target checkpoint's own smoke after fixing the control. Rerun both.

## TP8 Across Two Four-GPU Nodes

Use TP8 only after single-node format and behavior smoke passes. For two four-GPU nodes:

- Reserve both nodes for this validation and confirm eight visible idle GPUs.
- Use one consistent SGLang revision, checkpoint inventory, container image, and launch configuration.
- Assign global ranks 0 through 7 and local ranks 0 through 3 per node.
- Verify rendezvous address, port, node rank, network interface, and NCCL reachability before model load.
- Confirm every node can read every indexed shard, or stage an identical complete checkpoint locally on each node.
- Disable MNNVL only when the platform does not support the requested fabric path and logs identify that failure.
- Set `NCCL_SOCKET_IFNAME` only after inspecting routable interfaces on both nodes.
- Preserve both node logs and inspect all ranks; rank 0 health alone cannot prove rank survival.

Large checkpoints can exceed page-cache capacity even when filesystem capacity is sufficient. Stage or prefetch in bounded chunks, and verify that an attempted optimization does not make each node fetch only a topology-misdetected subset of shards.

Success requires all eight ranks alive through load and warmup, healthy HTTP service, one deterministic finite-output request, a stop finish reason, and zero new OOM events.

## Task Evaluation

After behavioral smoke, run a representative task suite through the standard evaluation harness. Gate on both accuracy and stop rate. Accuracy parsers may extract a correct answer from trailing runaway text, so a good score with poor stop rate is a failure.

Record dataset revision, sample count, prompt mode, decoding settings, reference baseline, accuracy, stop rate, truncation rate, error count, and report hashes. If the user explicitly waives a larger evaluation, preserve the smaller completed result and state the waiver; do not claim the larger suite passed.

## Private Publication

Before upload:

1. Resolve the authenticated identity without printing the token.
2. Confirm the intended owner or organization and private repository policy.
3. Check private storage quota against complete on-disk file bytes, not index payload bytes.
4. Create the destination as private and verify its visibility.
5. Use a resumable large-folder transfer supported by the hosting service.

After upload:

1. Wait for the uploader to exit successfully and record the remote commit.
2. Compare all remote and local filenames and exact file sizes.
3. Verify every metadata file hash, including config, index, tokenizer files, model card, conversion manifest, and `.gitattributes`.
4. Count all remote safetensors shards and compare with the index.
5. Recheck private visibility.

Inspect `.gitattributes` as text. An automatically appended LFS rule can be concatenated to a final line when the local file lacks a trailing newline, leaving malformed attributes even though payload upload succeeds.

Delete a superseded private repository only when the user explicitly authorizes deletion and the intended destination has passed all inventory and metadata checks. Confirm the deleted repository is absent and the retained repository remains complete and private.

## Failure Matrix

| Symptom | Likely cause | Required response |
|---|---|---|
| Full-model export OOM | Export unshards or retains complete fused experts | Return to bounded layer/expert streaming |
| GPU memory grows each layer | Slice model or temporary export remains referenced | Release objects, collect garbage, empty allocator cache |
| Resume skips stale output | Marker identity is incomplete | Bind source, dependency, recipe, calibration, range, size, and hash |
| Missing or duplicate experts | Global renaming or worker ranges overlap | Reject assembly and validate disjoint ranges |
| Unexpected dtype counts | Wrong preset or export handler | Require packed U8, FP8 block scales, and FP32 scales |
| Invalid scales | Bad slice, calibration, or partial export | Quarantine and regenerate the part |
| MTP becomes NVFP4 | Disable rule missing or ignore list overwritten | Disable `*mtp*` and preserve all exclusions |
| Loader finds no shards | Loader ignores nested indexed paths | Create verified root hard links and rewrite index atomically |
| Load passes but output is wrong | Calibration distribution or runtime state corruption | Compare real activation range and run known-good control |
| Correct answer never stops | EOS/runtime corruption or bad checkpoint behavior | Fail the gate; inspect raw response and stop metrics |
| Remote byte total differs | Header accounting, missing files, or malformed attributes | Compare exact inventory and metadata hashes before deletion |
| Multi-node load hangs partway | `LOCAL_SIZE` overwritten to global TP, so each node prefetches only a fraction of shards | Verify each rank's loaded-shard count, not just rank-0 health; pin `LOCAL_SIZE` to the per-node rank count |
| Multi-node load far slower than expected | Checkpoint prefetch threads exceed host page cache | Measure page-cache headroom and reduce prefetch threads (one per local rank is a safe starting point) |
| Pod evicted mid-validation on a shared cluster | Shared-filesystem ephemeral-storage quota hit while streaming a large checkpoint (observed on a shared-filesystem mount with a per-node eviction threshold) | Stream the checkpoint to local NVMe and remove the shared mount from the hot path before model load; treat the eviction as a platform issue |
| Checkpoint does not fit GPU memory even at high TP | NVFP4 experts-only checkpoint of this class is still of order 1.4 TB and does not fit on four Blackwell-class GPUs | Plan `--cpu-offload-gb` (of order 100 GiB per rank) as a hard requirement, and verify offloaded behavior with the known-good control — CPU offload is a common silent-corruption vector |
| "Passed" on a new Model Optimizer commit but numerics drifted | Calibration or export behavior changed between adjacent upstream commits while structure stayed compatible | Never validate a snapshot move by load alone; rerun the full gate chain (dry run → assembly → structural → scale → control + target smoke → accuracy + stop-rate) and reject any reuse of parts from the previous snapshot |
