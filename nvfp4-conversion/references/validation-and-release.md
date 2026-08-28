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

Run `scripts/audit_checkpoint.py` for unified-HF Model Optimizer output. It checks physical/index agreement, recipe metadata coverage, packed and scale forms (dense rank-2 and fused rank-3 expert bases, NVFP4 group size 16), positive finite scales, and logical equality of unchanged tensors in bounded row chunks. Quantized MTP, router, or gate bases fail the audit unless `--allow-quantized-protected` is passed with a dedicated recipe and independent qualification. A routed assembler must supply an equivalent `quantized_layers` precision contract when it does not emit `hf_quant_config.json`.

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

Confirm the runtime reads the quantization exclusions you think it reads. SGLang's ModelOpt config prefers the flat `config.json` `quantization_config` and its `ignore` key, while Model Optimizer writes `exclude_modules` only into `hf_quant_config.json` — an export whose exclusions live in the wrong key, or in checkpoint-namespace prefixes the runtime does not map (VLM `language_model.`, nextn `decoder`, vision `visual.`), has every module built as quantized. A `quantized_layers`-less scope recipe makes this invisible until a packed-vs-BF16 shape assert fires at load. Verify before serving by dumping a few deliberately-unquantized modules' built parameters; an excluded module instantiated as packed U8 with scale tensors means the exclusion metadata was not applied.

One Blackwell hybrid-model configuration qualified with full attention `trtllm_mha`, resolved page size 64, FP8 E4M3 KV, Mamba radix `extra_buffer`, static memory fraction 0.75, overlap/radix enabled, and decode CUDA Graph maximum batch 64. Static fractions 0.85 and 0.90 OOMed during MTP graph capture. This is model/hardware evidence, not a portable default.

Budget hybrid-state memory explicitly. In GDN/Mamba-style hybrids the intermediate SSM state at FP32 consumes twice the BF16 bytes; a combination such as a BF16 LM head with FP32 SSM state can exceed consumer-GPU capacity. Check the full configuration combination against measured memory before serving.

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

## Statistical Protocol

Single-seed comparisons are untrustworthy in both directions: the same checkpoint pair has flipped sign across single-seed reruns (+0.83pt versus -0.38pt on GSM8K), and a single-seed "significant" result does not survive multiple-comparison correction. For any release-affecting comparison:

- Use a paired design: identical request seeds in both arms, same device or sequential order on the same hardware, question+seed stratified paired bootstrap, and exact McNemar. Never pool generations across arms as independent samples.
- Predeclare a directional degradation test and a non-inferiority margin (for example 1pt on GSM8K-class, 5pt on agentic-class benchmarks). "Failed to reject equality" is not equivalence, and significant degradation below the margin can hold simultaneously.
- Include a BF16 anchor arm so a gap can be attributed — the reference may have risen to parity rather than the candidate falling.
- Runs under different KV dtypes or backends are different strata, not seeds of one condition. Pool them descriptively, never inferentially.
- sgl-eval sends one constant request seed per run and `--n-repeats` repeats are byte-identical. Multi-seed pairing requires independent CLI-level runs with `--n-repeats 1`. Rerunning with the same server and request seeds should reproduce historical scores exactly; use that as an endpoint determinism check.
- Saturated benchmarks (GSM8K at 97%+) barely discriminate. Include a non-saturated benchmark (GPQA-Diamond-class, or AIME-class with repeats) and estimate power from the observed discordance rate before claiming parity.

## Thinking Cost And Agentic Gate

For reasoning models the dominant quantization cost can be thinking-token inflation rather than accuracy: measured +17% (FP8) and +19% (NVFP4) mean thinking tokens at equal accuracy, with flat p50 and the cost concentrated in the long-question tail. The frozen gate for a reasoning model must include:

- Stop rate, the earliest sensitive indicator.
- Thinking-length distribution: p50, tail, and length-finish rate. Mean length is polluted by runaways; per-question matched-seed pairing is the tight test.
- One agentic long-horizon benchmark. A checkpoint can hold GSM8K/AIME-band accuracy while losing ~20pp agentic pass@1 through verbosity-driven timeouts (multiplied timeout rate, zero repetition loops); pass@k does not rescue a systematic timeout failure.
- The explicit `max_tokens` used. Truncation fabricates regressions — an apparent 5pt AIME drop vanished when the cap rose from 32K to 131K. Scores with material length-finishes are unacceptable; raise the cap until truncation disappears or annotate the limitation honestly.

## Attribution

- Change one variable at a time (LM head x KV dtype x SSM dtype x speculative). Flipping a whole configuration bundle at once can never attribute a regression.
- A dtype flag may drag automatic backend selection with it (observed: FP32 SSM state selecting Triton, BF16 selecting FlashInfer). For kernel-level attribution, pin identical backends in both arms.
- On agentic benchmarks, KV-cache dtype has hurt scores more than weight format (~3-5pp from FP8 KV, while GSM8K-class benchmarks were insensitive to every one of these factors). Do not extrapolate sensitivity from a saturated benchmark.
- Before blaming the checkpoint, exclude the serving stack: the same checkpoint scored 46% on SM100 versus 96.89% on SM90 due to a kernel bug, and token-spam with acceptance collapse traced to a speculative-fusion race in the runtime. A known-good control checkpoint on the identical stack is the cheapest exclusion.

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

Use `scripts/inventory.py` and `scripts/compare_inventories.py` before durable promotion. After upload, pass the credential over stdin to `scripts/verify_hf.py`; it checks authorized visibility, exact filename and size sets, downloaded metadata SHA256, exposed LFS SHA256, and the resolved commit.

### Derived Variants

Quantizing the LM head blocks draft tools that consume it (EAGLE-style draft heads and similar speculative tooling) from working out of the box, while a BF16 LM head costs measurable decode throughput (~28% with MTP-class drafting, ~10% with block-draft-class tooling) plus model size. The measured resolution is a two-checkpoint release: keep the FP4 LM head in the main repository and publish a separate BF16-LMHead derivative. Never overwrite the main repository in place, and retire the workaround variant with an ordinary revert commit on main once the upstream runtime limitation is fixed — no force push.

A BF16-LMHead variant must rewrite only the touched shards; update `quantization_config` group targets and `quantized_layers`, `hf_quant_config.json`, the conversion manifest, and the model card quantization section consistently; rebuild the safetensors index; and verify index/header key-set equality.

Config-only or tokenizer-only fixes do not invalidate existing evaluation scores; any weight-touching rebuild produces new bytes and inherits nothing. Label every score in the model card with the artifact identity it belongs to.

### Tokenizer And Config Battery

Model Optimizer may regenerate `tokenizer.json` in an older format (pre-tokenizer regex missing `\p{M}`, flipped ByteLevel decoder flags), drop processor files, or emit a stray legacy config. Before publication, diff every non-weight file against the official source repository and verify token-id equality on tricky strings: combining marks, Hangul, Thai, and ZWJ emoji sequences.

### Large-Upload Notes

The hub xet pipeline has failed deterministically on large checkpoints; fall back to git plus git-lfs. Enable `hf lfs-enable-largefiles` before pushing files over 5GB, `git lfs track` an oversized `model.safetensors.index.json`, and lower `lfs.concurrenttransfers` to resume past broken pipes.

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
| Single-seed comparison flips sign across reruns | Insufficient statistical power | Paired multi-seed protocol with a predeclared margin |
| Equal accuracy but worse agentic scores | Thinking-token inflation | Gate on thinking-length distribution, stop rate, and an agentic bench |
| Score regression vanishes at higher `max_tokens` | Length truncation | Raise the cap until length-finishes disappear |
| Draft tools reject the checkpoint | Quantized LM head | Publish a BF16-LMHead derivative |
| Tokenizer behavior differs from source | Regenerated legacy `tokenizer.json` | Diff non-weight files; check token ids on tricky strings |
| Same checkpoint scores differ across GPU generations | Serving-stack or kernel defect | Run a known-good control before blaming the checkpoint |
