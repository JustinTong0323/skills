# Validation And Release

Every validation run must create an immutable qualification record that references one exact `assembly_manifest_sha256`. Record validator, SGLang, kernel, backend, template, dataset, and harness identities here rather than in part markers. A newer validator or runtime produces a new qualification record and reruns the affected gates without invalidating routed parts or the assembled checkpoint.

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

Require output non-routed keys to equal the complete source non-routed set. Compare each non-routed tensor's shape, dtype, and canonical logical content digest with the source. Require every MTP tensor to remain present, BF16, and content-identical. The validator must independently reread the output; assembly-time digests alone do not validate written files.

Require the resolved text-config path, backbone prefix, and canonical-to-source mapping to match the assembly manifest and its referenced conversion manifest. Verify the complete auxiliary metadata inventory by path, declared origin, size, and SHA256.

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

Before sending a request, hash the effective standalone or embedded chat template and parse-render the exact prompt mode. Use a template-supported deterministic mode, temperature 0, bounded `max_tokens`, and token logprobs. Disable thinking only when the effective template supports that option; if it rejects disabled thinking, use its supported thinking mode with a bounded budget. Record the template hash, selected entry point, template kwargs, prompt, and sampling parameters.

Require the expected answer, `finish_reason=stop`, at least one token logprob, and all logprobs finite. Save the raw response before enforcing assertions so failures remain diagnosable. A template-render rejection is a metadata or request-contract failure, not checkpoint numeric evidence.

Finite output alone is insufficient. Repetition, a wrong answer, `finish_reason=length`, or missing EOS fails the behavioral gate.

## Runtime Control Test

When a structurally valid checkpoint loads but behaves incorrectly, run the identical launch and request against a known-good ModelOpt NVFP4 checkpoint from the same model family that is available under the applicable access and disclosure policy. Hold topology, kernels, offload, prompt, and sampling constant.

If both checkpoints fail only with CPU offload enabled, investigate registered parameter aliases and stale tensor views before blaming conversion. One observed failure came from an unregistered view retaining pre-load GPU storage after the registered parameter was offloaded and replaced. The control passed again when the runtime kept the alias registered and derived the required view at use time.

Do not waive the target checkpoint's own smoke after fixing the control. Rerun both.

## MTP Runtime Gate

Run this gate when the release serving recipe enables MTP, NEXTN, EAGLE, or another draft path backed by the checkpoint's preserved MTP tensors. It is not required for a target recipe that intentionally serves without MTP.

Require logs to prove that the expected MTP model class loaded, draft state or KV buffers were allocated, and draft prefill, decode, extend, and verify kernels or graphs initialized. Run a bounded representative workload and record speculative steps, draft tokens, sampling parameters, context-length distribution, acceptance length, acceptance rate, positive-acceptance sample count, throughput, and errors.

Low or workload-dependent acceptance does not by itself prove corrupt MTP weights. Compare workloads and sampling settings before assigning cause. Claim an isolated MTP speedup only from an MTP-on/off A/B that holds the checkpoint, runtime revision, kernels, topology, graph settings, prompts, sampling, and concurrency constant.

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

After behavioral smoke, run a representative task suite through the standard evaluation harness. Before launching it, freeze an acceptance record containing dataset revisions, sample counts, prompt and decoding settings, reference baseline revision, minimum accuracy or maximum allowed regression, minimum stop rate, maximum truncation rate, and maximum error rate. Changing these criteria after observing target results requires an explicit waiver, not a rewritten pass condition.

Gate on every predeclared criterion. Accuracy parsers may extract a correct answer from trailing runaway text, so a good score with poor stop rate is a failure.

Record the acceptance-record hash, dataset revision, sample count, prompt mode, decoding settings, reference baseline, accuracy, stop rate, truncation rate, error count, verdict, and report hashes in the qualification record. If the user explicitly waives a larger evaluation, preserve the smaller completed result and state the waiver; do not claim the larger suite passed.

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
3. Verify every metadata file hash, including config, index, tokenizer files, model card, conversion and assembly manifests, qualification record, and `.gitattributes`.
4. Count all remote safetensors shards, compare with the index, and verify each remote cryptographic object identity when the hosting service exposes one. Exact size alone is not byte verification.
5. Recheck private visibility.

If auxiliary metadata is later aligned to a canonical release, publish it as a separate revision. Compare before-and-after inventories, require all weight shards, the index, and quantization metadata to remain unchanged, validate standalone and embedded chat-template behavior, and rerun the API smoke against the new revision.

Inspect `.gitattributes` as text. An automatically appended LFS rule can be concatenated to a final line when the local file lacks a trailing newline, leaving malformed attributes even though payload upload succeeds.

Delete a superseded private repository only when the user explicitly authorizes deletion and the intended destination has passed all inventory and metadata checks. Confirm the deleted repository is absent and the retained repository remains complete and private.

## Failure Matrix

| Symptom | Likely cause | Required response |
|---|---|---|
| Full-model export OOM | Export unshards or retains complete fused experts | Return to bounded layer/expert streaming |
| GPU memory grows each layer | Slice model or temporary export remains referenced | Release objects, collect garbage, empty allocator cache |
| Resume skips stale output | Wrong generation selected or manifest is mutable | Resume by full conversion-manifest digest and verify part size and hash |
| Missing or duplicate experts | Global renaming or worker ranges overlap | Reject assembly and validate disjoint ranges |
| Unexpected dtype counts | Wrong preset or export handler | Require packed U8, FP8 block scales, and FP32 scales |
| Invalid scales | Bad slice, calibration, or partial export | Quarantine and regenerate the part |
| MTP becomes NVFP4 | Disable rule missing or ignore list overwritten | Disable `*mtp*` and preserve all exclusions |
| Source shard changes under the same index | Source payload is not content-bound | Start a new generation from a verified source-file manifest |
| Non-routed tensor has correct metadata but wrong values | Assembly copied or wrote the wrong payload | Compare canonical per-tensor content digests after reopening output |
| Valid source keys are not found | Backbone prefix or text config is nested | Discover one complete prefix and config path from metadata; reject fixed-offset parsing |
| Deterministic smoke fails before inference | Effective template rejects the requested thinking mode | Parse-render first and use a supported deterministic template contract |
| Published prompt behavior changes | Auxiliary files came from mixed or moving revisions | Bind every metadata file to an origin and hash; align only in a separate verified revision |
| MTP loads but acceptance collapses | Sampling or long-context workload differs from the qualification gate | Record workload-specific acceptance and use a controlled MTP-on/off A/B before claiming regression or speedup |
| Accuracy result is interpreted after the run | Acceptance criteria were not frozen before evaluation | Record and hash thresholds before sending target requests; changes require a waiver |
| Validator update forces expert reconversion | Qualification identity was mixed into part identity | Create a new qualification record and rerun only the affected gates |
| Metadata or assembler update forces expert reconversion | Assembly identity was mixed into part identity | Create a new assembly manifest and reuse verified parts from the conversion generation |
| Loader finds no shards | Loader ignores nested indexed paths | Create verified root hard links and rewrite index atomically |
| Load passes but output is wrong | Calibration distribution or runtime state corruption | Compare real activation range and run known-good control |
| Correct answer never stops | EOS/runtime corruption or bad checkpoint behavior | Fail the gate; inspect raw response and stop metrics |
| Remote byte total differs | Header accounting, missing files, or malformed attributes | Compare exact inventory and metadata hashes before deletion |
| Multi-node load hangs partway | `LOCAL_SIZE` overwritten to global TP, so each node prefetches only a fraction of shards | Verify each rank's loaded-shard count, not just rank-0 health; pin `LOCAL_SIZE` to the per-node rank count |
| Multi-node load far slower than expected | Checkpoint prefetch threads exceed host page cache | Measure page-cache headroom and reduce prefetch threads (one per local rank is a safe starting point) |
| Pod evicted mid-validation on a shared cluster | Shared-filesystem ephemeral-storage quota hit while streaming a large checkpoint (observed on a shared-filesystem mount with a per-node eviction threshold) | Stream the checkpoint to local NVMe and remove the shared mount from the hot path before model load; treat the eviction as a platform issue |
| Checkpoint does not fit GPU memory even at high TP | NVFP4 experts-only checkpoint of this class is still of order 1.4 TB and does not fit on four Blackwell-class GPUs | Plan `--cpu-offload-gb` (of order 100 GiB per rank) as a hard requirement, and verify offloaded behavior with the known-good control — CPU offload is a common silent-corruption vector |
| "Passed" on a new Model Optimizer commit but numerics drifted | Calibration or export behavior changed between adjacent upstream commits while structure stayed compatible | Start a new generation and rerun the full gate chain (dry run → assembly → structural → scale → control + target smoke → accuracy + stop-rate) |
