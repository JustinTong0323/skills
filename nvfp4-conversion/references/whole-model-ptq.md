# Whole-Model PTQ And Export

Use this path for dense models and any supported model that fits the complete Model Optimizer PTQ/export lifecycle with measured headroom.

## Environment

Keep site-local values outside shared source control:

```bash
export CONVERSION_ROOT="<workspace>"
export MODELOPT_ROOT="<modelopt-checkout>"
export REQUESTED_MODELOPT_REF="<commit-or-moving-ref>"
export SOURCE_CHECKPOINT="<read-only-source>"
export OUTPUT_STAGING="<same-filesystem-staging-output>"
export OUTPUT_CHECKPOINT="<final-output>"
export PYTHON_BIN="<isolated-python>"
```

Resolve `REQUESTED_MODELOPT_REF` to an exact detached commit. Run conversion with `PYTHONNOUSERSITE=1`, `TOKENIZERS_PARALLELISM=false`, and `PYTHONPATH` pointing to that checkout. Record package and GPU versions before calibration.

## Official Path Discovery

Find the architecture-specific Model Optimizer example and recipe at the pinned commit. Read both the example entry point and the recipe body. Establish:

- Supported Transformers model class and config structure.
- Load/device-map behavior and required trust-remote-code policy.
- Recipe module map, exclusions, KV-cache declaration, calibration method, and exporter.
- Exact CLI arguments and whether dataset/sample/sequence values are honored by the entry point.

Prefer the official architecture adapter and unified HF exporter. Do not adapt the routed-expert slice model for a dense checkpoint.

## Resource Preflight

Check GPU, host, cgroup, and filesystem headroom. Budget separately for:

- Source model load.
- Calibration batches and cached activations.
- Quantizer state.
- Export buffers and temporary shards.
- Final output plus an independent durable copy.

Run a bounded real-weight preflight through the same code. Monitor peak GPU/host memory and cgroup OOM counters. If the production sample count increases retained state, scale the budget from measurement rather than source file size alone.

## Production Conversion

Start from an empty generation-specific staging directory. A representative invocation shape is:

```bash
env \
  PYTHONNOUSERSITE=1 \
  PYTHONPATH="$MODELOPT_ROOT" \
  TOKENIZERS_PARALLELISM=false \
  "$PYTHON_BIN" <OFFICIAL_PTQ_ENTRYPOINT> \
    --model "$SOURCE_CHECKPOINT" \
    --quantization <EXACT_RECIPE> \
    --dataset <DATASET> \
    --num-calib-samples <COUNT> \
    --calib-batch-size <BATCH> \
    --calib-seq-len <LENGTH> \
    --seed <SEED> \
    --export-path "$OUTPUT_STAGING"
```

Actual option names come from the pinned entry point. Record the exact executed argv as structured data without credentials.

Capture stdout/stderr and exit status. A zero exit code permits audit; it is not a pass.

## Real Activation Capture

When the architecture cannot synthesize calibration (for example no post-attention RMSNorm), capture real hidden-state activations from the serving path. Observed pitfalls:

- Calling `.cpu()` inside a forward hook during CUDA graph capture crashes. Guard with `torch.cuda.is_current_stream_capturing()` or capture with `--disable-cuda-graph`.
- Server-side retokenization does not preserve prompt row counts. Record the actual row count in the capture contract and verify it per layer.
- `ndarray.tofile()` truncates an existing file; append through an `"ab"` handle.

## Sensitive-Layer Localization

When quality regresses after conversion, localize sensitive layers instead of guessing exclusions: teacher-force the same prompt batch through the floating-point source and the quantized model, hook every layer's hidden-state output, and rank layers by |Δh|/|h|. Measure hidden states, never final logits — final norm and the LM head can mask cross-layer divergence (observed amplification from 0.65% to 62% across layers). Move the top-diverging layers into the recipe ignore list, reconvert as a new generation, and judge the result by thinking-length distribution and stop rate rather than accuracy alone.

## Whole-Model Audit

Independently reopen the exported files. In addition to the shared audit, require:

1. Output logical key set equals the expected transformed source key set.
2. Each normalized recipe module base has exactly the expected packed weight and scale forms.
3. FP8 modules have their expected FP8 weights/scales and are not miscounted as NVFP4.
4. Every excluded/unchanged source tensor is content-identical after reopening output.
5. MTP tensors follow the declared policy.
6. Quantization metadata names every transformed module consistently with the actual shard keys.

Do not hard-code counts from a sibling model. Derive expected bases from current source keys and normalized recipe match rules, then compare actual transformed bases.

For W4A16 NVFP4, validate group size, packed dimensions, U8 packed data, FP8 E4M3 block scales, FP32 secondary/input scales where the exporter emits them, and all scale values finite and positive. For W4A4 `NVFP4`, additionally require one finite, strictly positive F32 `input_scale` per quantized base; its absence means the export is W4A16 regardless of metadata labels.

For mixed-precision recipes, report separate W4A16, FP8, and unchanged counts and bytes. A runtime may resolve the checkpoint as `modelopt_mixed` even when the CLI loader flag is `modelopt_fp4`; record both values.

## Durable Promotion

Write the audited candidate to local staging, copy it to a uniquely named durable staging path, and compute complete inventories on both sides. Require byte-for-byte inventory equality before runtime qualification.

After qualification passes, atomically rename durable staging to the final path on the same filesystem. Never overwrite an existing final path and never call an unaudited directory final.

## Failure Modes

| Symptom | Required response |
|---|---|
| Official model class is unsupported | Stop or select a separately implemented path; do not force a nearby adapter |
| Process exits zero but shard/key counts differ | Fail audit and preserve staging/logs |
| MTP was quantized by a wildcard | Correct the recipe, create a new generation, and reconvert |
| Weight packing passes but scales contain zero/non-finite values | Fail and inspect calibration/export numerics |
| Full model loads but production calibration OOMs | Rebudget or use an official offload/distributed path; routed streaming is valid only for its supported fused layout |
| Runtime reports `modelopt_mixed` | Compare with mixed recipe metadata; do not require a misleading single-algorithm label |
| FP8 KV runtime warns that scales are absent | Record the fallback and gate quality on that exact behavior |
| Structure passes but output is repetitive/degenerate | Suspect calibration, not export; Gaussian or unrepresentative calibration can saturate scales 5x while passing every structural check |
| Quality regresses on long reasoning tasks | Localize sensitive layers by per-layer hidden-state divergence and exempt them in a new generation |
