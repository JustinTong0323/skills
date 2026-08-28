# Online NVFP4 Serving

Source: LMSYS blog "Towards Blackwell-Native 8-bit and 4-bit RL" (2026-07-29), SGLang PR sgl-project/sglang#26083 (merged 2026-06-10), implementation `python/sglang/srt/layers/quantization/nvfp4_online.py`.

## What It Is

`--quantization nvfp4_online` converts eligible MoE expert weights to NVFP4 during weight loading and computes FP32 activation scales per token at runtime. There is no calibration pass and no static per-tensor `input_scale`. Scope and constraints:

- Sources: BF16/FP16 checkpoints directly, FP8 checkpoints via load-time dequant-then-requant (MXFP8 sources rejected).
- Weights use the same static two-level contract as offline conversion: FP32 `weight_scale_2 = amax / (e4m3_max * 6)` plus E4M3 per-16-block scales.
- The per-token FP32 activation scale is fused into the FlashInfer activation-quantization kernel, so online scaling adds no separate pass and no exposed decode latency.
- SM100+ only; MoE experts only (dense linears keep source precision); backends `flashinfer_trtllm`, `flashinfer_trtllm_routed`, or cutedsl without A2A / with FlashInfer A2A.
- Gated w1/w3 shards are quantized as one concatenated tensor so both share a single amax-derived FP32 scale, matching the serialized-checkpoint convention.
- `SGLANG_FP4_IGNORED_LAYERS` keeps selected MoE layers or shared experts in high precision at serve time, without reconversion.

## When It Replaces Offline Conversion

Prefer the online path over running this skill's calibration pipeline when all are true:

- The goal is serving on Blackwell with a supported MoE backend, not publishing a distributable pre-quantized artifact.
- The source is BF16/FP16 or FP8 and only expert weights need quantization.
- No consumer requires a serialized NVFP4 checkpoint (other runtimes, RL weight-update pipelines needing bit-exact train/inference quantizers, CUTLASS-only MoE paths).

Offline Model Optimizer conversion remains necessary for publishable pre-quantized checkpoints, unsupported online backends, W4A16 or dense-linear quantization scopes, and weight-side recipes that must be baked in (for example four_over_six block selection, though a runtime `FLASHINFER_NVFP4_4OVER6` hook exists).

## Why It Avoids Our Calibration Failure Modes

Every stage of the offline pipeline that has produced a silent-garbage incident is absent from the online path:

- No calibration data at all, so synthetic or unrepresentative activations cannot poison scales (the Gaussian-calibration failure class). Weight scales come from the weights themselves; activation scales are computed per token at runtime.
- No exported scale tensors beyond the weight-side contract, so there is no static `input_scale` to leave uncalibrated or miswired, and no attention `k_scale`/`v_scale` export to leak into the runtime.
- Only expert weights are touched, so attention, router, shared experts, MTP, and embeddings are never in the conversion blast radius.

Per-token activation scaling is also the principled choice, not just the convenient one: a per-tensor FP32 activation scale makes a token's quantized representation depend on batch composition and can share scale information across tokens (Cursor Composer 2 report). Computing one FP32 scale per token online removes both the calibration artifact and the cross-token coupling.

## Precision Priors From The RL Recipe Ablations

Transferable serve-time and conversion-scope guidance, validated on Qwen3-30B-A3B GRPO ablations (8x B200):

- Keeping the last ~15% of layers in BF16 meaningfully reduces train-inference mismatch; BF16 on early layers showed no such benefit.
- Shared experts should stay high precision: they are always active, so their quantization error hits every token. Routed-experts-only scope remains correct.
- For MLA models, keep `kv_b_proj` (and absorbed k/v up-projections) BF16: absorbed and non-absorbed modes use different contraction axes, which changes which elements share a 1D microscaling block scale.
- Reward curves for MXFP8 and per-token NVFP4 MoE track BF16 closely; the diagnostic ref-KL is higher for low precision but stays in a reasonable range.
- RL weight updates require a bit-exact quantizer contract between training (TransformerEngine) and rollout (FlashInfer): set `FLASHINFER_DISABLE_FP4_QUANT_FAST_MATH=1`. Serving-only use of fast math is fine.

## Recommended Evaluation Before Retiring Calibration

Before treating the online path as a replacement for a given model family, A/B it against the calibrated offline artifact with the same gate chain (smoke battery, stop rate, GSM8K-class accuracy with paired multi-seed protocol, thinking-length distribution, one agentic benchmark):

- Arm A: calibrated Model Optimizer checkpoint served with `modelopt_fp4`.
- Arm B: the BF16/FP8 source checkpoint served with `--quantization nvfp4_online`.

Also use `SGLANG_FP4_IGNORED_LAYERS` to test sensitive-layer hypotheses at serve time instead of reconverting; it subsumes the queued per-layer |Δh|/|h| high-precision reconversion experiment.
