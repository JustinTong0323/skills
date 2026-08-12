# Validation And Release

Every run produces an immutable qualification record against one exact output manifest. Conversion, output, qualification, and release inventory are separate identities.

## Structural Validation

Parse JSON with duplicate-key rejection. Require:

- The explicit index, or implicit one-file key map, equals the union of physical safetensors keys.
- Every indexed file exists, every physical shard is indexed when an index is present, and no key appears twice.
- No temporary, partial, or incomplete files.
- Output key set equals the recipe-derived transformed source key set.
- Module-base counts match normalized quantization metadata.
- Packed weights, FP8 weights, scales, and unchanged tensors have expected shapes/dtypes.
- Config and quantization metadata agree with actual tensors.

For every tensor declared unchanged, compare a canonical logical content digest after independently reopening source and output. Shape and dtype equality are insufficient. Require complete MTP presence, policy-compliant dtype, and content equality when preserved.

Scan all recipe scale tensors with bounded CPU workers. Require finite, strictly positive values and record count/min/max.

## SGLang Runtime Gate

Launch the target SGLang revision on a devbox, not a local workstation. Explicitly set only arguments required by the qualified model/hardware combination. Record the exact command and serialized resolved server arguments.

A generic launch shape is:

```bash
python3 -m sglang.launch_server \
  --model-path "$OUTPUT_CHECKPOINT" \
  --served-model-name nvfp4-smoke \
  --quantization modelopt_fp4 \
  --fp4-gemm-backend <qualified-backend> \
  --tensor-parallel-size <tp>
```

Do not copy backend names from another architecture or GPU generation. Confirm from resolved args and logs:

- Expected target model class and mixed/NVFP4 quantization metadata loaded.
- Requested full-attention, MoE, linear-attention, FP4 GEMM, KV-cache, and speculative backends initialized.
- Page size, memory fraction, scheduling/cache mode, and graph batch limits resolved as intended.
- All shards/ranks loaded and warmup/graphs completed, or graph disablement was intentional.
- `/health` succeeds and GPU/cgroup OOM counters have not changed.

Backend namespaces matter. In a hybrid model, full attention may use `trtllm_mha` while GDN/linear attention reports Triton. Multimodal preprocessing may have another backend. Diagnose each field from resolved server args instead of treating any `triton` log line as the main attention backend.

One Blackwell hybrid-model configuration qualified with full attention `trtllm_mha`, resolved page size 64, FP8 E4M3 KV, Mamba radix `extra_buffer`, static memory fraction 0.75, overlap/radix enabled, and decode CUDA Graph maximum batch 64. Static fractions 0.85 and 0.90 OOMed during MTP graph capture. This is model/hardware evidence, not a portable default.

## Deterministic Smoke

Hash and parse-render the effective standalone or embedded chat template before inference. Use a supported deterministic mode, bounded `max_tokens`, and token logprobs. Disable thinking only if the effective template supports it.

Save raw responses before assertions. Require an exact simple answer, `finish_reason=stop`, at least one token logprob, and every logprob finite. Wrong output, repetition, missing EOS, template rejection, or length finish fails.

When target behavior is suspect, run the identical request and runtime configuration against a known-good same-family checkpoint allowed by the disclosure policy. The target must still pass after a control fix.

## MTP And Speculative Gate

Run when release serving enables MTP/NEXTN/EAGLE or another checkpoint-backed draft path. Require:

- Expected draft model class and preserved MTP tensors loaded.
- Draft KV/state buffers allocated.
- Draft prefill/decode/extend and target verify kernels or graphs initialized.
- A bounded representative workload with average speculative acceptance length above 1.0 and positive accepted-token evidence.
- Errors, sampling, context distribution, steps, top-k, draft tokens, acceptance length/rate, and throughput recorded.

Do not infer a speedup from acceptance alone. Performance claims require an MTP on/off A/B holding checkpoint, runtime, kernels, topology, graph settings, prompts, sampling, and concurrency constant.

## FP8 KV Cache Evidence

Inspect exported metadata and runtime logs. If SGLang warns that no KV scaling factors were provided and defaults to `1.0`, record the warning verbatim in machine-readable qualification. Do not fabricate scale evidence or silently remove FP8 KV from the tested command.

The warning is a risk, not an automatic pass or fail. The exact configuration must satisfy the frozen task-quality, stop, truncation, error, and empty-output gates. A later checkpoint with calibrated KV scales requires a new conversion/output identity and requalification.

## Task Evaluation

Before sending target requests, freeze:

- Dataset revision and exact sample count.
- Prompt/template and thinking mode.
- Sampling values, including whether omitted values intentionally use checkpoint defaults.
- Reference checkpoint/revision and baseline result.
- Minimum accuracy or maximum allowed regression.
- Minimum stop rate and maximum truncation, request-error, and empty-output rates.
- Harness revision/diff and output paths.

Run the complete dataset through the standard harness. Verify the expected row count, exactly one complete prediction file, metrics/prediction hashes, completion-token totals, finish-reason distribution, empty outputs, and non-partial status. Missing optional `partial` metadata is not proof of a partial run when the row/file/count contract proves completeness.

Accuracy alone cannot pass. Evaluators may extract a correct answer from runaway text. Apply every frozen criterion without post-hoc changes. A threshold change after results is a waiver and must not be labeled pass.

## OOM And Intentional Shutdown

Capture cgroup memory events before startup, after load, after evaluation, and after shutdown. Distinguish an OOM kill from an intentional `SIGTERM`/`SIGKILL`: wrapper exit `137` after an explicit teardown is not OOM when `oom_kill` remains unchanged. Record both the action and counters.

## Durable Promotion

Keep candidate and durable staging inventories identical before qualification. After pass:

1. Add model card, conversion manifest, tensor audit, frozen criteria, and qualification record to both copies.
2. Regenerate complete release inventories and require equality.
3. Preserve raw conversion/server/eval logs, responses, predictions, scripts, and reports in a separate durable evidence directory with `SHA256SUMS`.
4. Atomically rename staging to final paths.
5. Recompute final local and durable inventories.

Do not put large one-shot evaluation outputs in the model repository unless the publication contract requires them.

## Publication

Before upload:

1. Resolve authenticated identity without printing the token.
2. Confirm destination owner, source disclosure policy, storage quota, authorized visibility, and target nonexistence or expected revision.
3. Default to private when visibility is unspecified. Create the destination with the authorized visibility and immediately query it back. A private or unreleased source can never be published publicly by this workflow.
4. Use a resumable large-folder upload.

Pass credentials over stdin or a protected secret channel. Do not put a token in argv or a file. If upload fails after repository creation, do not broaden visibility; preserve the destination and resume safely.

After upload:

1. Wait for a successful commit and record its SHA.
2. Query the authenticated API with file metadata and require visibility to equal the authorized value.
3. Compare exact remote/local filename and byte-size sets.
4. Download every non-weight file at the committed revision and compare SHA256.
5. Compare each safetensors remote LFS/Xet SHA256 with its local SHA256 when exposed.
6. Recheck shard coverage and effective visibility.

Hugging Face may append an LFS rule for a large tokenizer to `.gitattributes`, changing the remote byte total. Download and inspect the file. If valid, synchronize the exact normalized file into local and durable checkpoints, regenerate inventories, and repeat full verification. Preserve a trailing newline so appended rules cannot concatenate.

Delete no source, staging, or prior repository without explicit authorization and successful independent destination verification.

## Failure Matrix

| Symptom | Likely cause | Required response |
|---|---|---|
| Zero exit but wrong tensor count | Exporter/recipe mismatch | Fail independent structural audit |
| Structure passes but output is wrong | Calibration or unchanged-content corruption | Compare real activation ranges and logical tensor digests |
| MTP loads but accepts no drafts | Sampling/workload mismatch or MTP/runtime defect | Record evidence and run controlled diagnostics; do not claim MTP pass |
| Main backend appears wrong | Linear/MM backend log was mistaken for full attention | Inspect serialized resolved server args by backend namespace |
| Draft KV allocation misses by a small amount | Static pool leaves insufficient post-target headroom | Use measured safe memory fraction and rerun complete qualification |
| FP8 KV scale warning | Recipe exported metadata without scale tensors | Record fallback and gate exact runtime behavior |
| Correct answers do not stop | EOS/template/runtime/checkpoint defect | Fail stop gate despite accuracy |
| Remote total bytes differ | Metadata normalization or missing file | Diff exact per-file inventory; inspect `.gitattributes` |
| Remote shard sizes match only | Byte identity was not checked | Compare exposed LFS/Xet object SHA256 |
| Server wrapper exits 137 after teardown | Intentional child termination or OOM | Correlate action timestamp with cgroup `oom_kill` |
