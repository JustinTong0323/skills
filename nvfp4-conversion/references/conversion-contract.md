# Conversion Contract

## Dependency Boundary

Use unmodified NVIDIA Model Optimizer source resolved to an exact commit. Commits with full gate-chain qualification on Blackwell-class hardware:

```text
87c9f8cf83021957d1a1a575c90c9a4eaaf7ef0c   # whole-model + routed-expert qualification (pre-0.46 main)
43fd41a58d52c4e6e5dec1d1ff5989ecc737ae1a   # 0.46.0: whole-model W4A4 on a 300B-class hybrid MoE
                                          # (unfused experts, KDA+DSA+MLA attention, MTP layer),
                                          # standard and four_over_six recipes; audit, serve/smoke,
                                          # and paired multi-seed quality gates passed 2026-08-27
```

Treat these as reference snapshots, not promises for every architecture. Resolve a requested moving ref once at the start of a fresh generation. Never let it advance during conversion or resume.

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

Use `scripts/preflight.py` and `scripts/inventory.py` for the deterministic portions of this discovery. Supply Model Optimizer support, measured whole-model fit, and routed-exporter qualification as explicit evidence; the script must not infer them from a model name.

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

- `NVFP4` (W4A4), including group size and output packed/scale dtypes. Every quantized base must carry a finite, strictly positive F32 `input_scale` in addition to the packed U8 weight, FP8 E4M3 group scales, and F32 global scale.
- `W4A16_NVFP4`, including group size and output packed/scale dtypes. A W4A16 base has no `input_scale`.
- `FP8`, including expected weight and scale forms.
- Source dtype, content unchanged.
- Excluded from quantization for a documented reason.

The per-base `input_scale` is the structural difference between W4A4 and W4A16. Never relabel a `W4A16_NVFP4` export as `NVFP4` by editing metadata; a W4A4 product requires a fresh conversion from the floating-point source with an activation-quantizing recipe. The audit rejects a missing `input_scale` on an `NVFP4` base and a stray one on a `W4A16_NVFP4` base.

Decide the product before choosing the recipe: `NVFP4` serving requires SM100+ hardware, while `W4A16_NVFP4` takes the Marlin FP4-A16 path with BF16 activations and does not use the native Blackwell FP4 GEMM. Do not quantize routers to a higher dtype than their source; an on-disk BF16 router upcast to FP32 buys nothing and costs measurable throughput.

Treat KV-cache quantization separately from weight conversion. A recipe name ending in `kv_fp8_cast` may write only metadata and no per-layer KV scale tensors. Audit what was exported, then record the runtime behavior. Do not claim calibrated KV scales when the runtime reports fallback scales of `1.0`.

MTP policy must be explicit. If excluded, require source-identical content. If quantized, require a dedicated supported recipe and independent runtime qualification; never let MTP be quantized by an accidental wildcard.

### Adaptive Block Scaling (4/6) — A/B-Gated Option

Adaptive block scaling ("Four Over Six", arXiv:2512.02010) quantizes each weight block twice — scaled to a maximum of 6 and of 4 — and keeps the version with lower per-block MSE, reducing error on the near-maximal values that dominate NVFP4 degradation. The exported format is unchanged (packed E2M1, E4M3 block scales, FP32 tensor scale, with block scales normalized by 256 instead of 448 for M=4 headroom), so runtime kernels and the tensor audit contract are unaffected; only scale selection changes.

**The standard recipe is the default; 4/6 is an optional variant gated on a per-family controlled A/B.** The paper's PTQ gains are perplexity and simple-task results on small dense models. Two production-scale family data points now bracket the outcome space:

- **Harmful (300B-class hybrid MoE, unfused experts, 36,423 NVFP4 bases, ModelOpt 0.46.0, official-FP8-scope W4A4)**: true 4/6 AIME26 88.85% vs 92.45% abs-max (1,920 question×repeat×seed pairs, delta -3.59pt, 95% CI [-5.10, -1.93], McNemar p<0.0001), +14% median and +22% mean thinking tokens (p90 tail 95,570 vs 54,199), stop rate 92.0% vs 95.2%. GSM8K a wash on accuracy (-0.13pt) but +12% mean thinking.
- **Non-inferior (745B-class GLM MoE, fused experts, MLA+MTP, 57,600 NVFP4 bases, calib 64)**: paired vs std-max on Terminal-Bench 2.1 (70/78 vs 70/78 same-task, flips 5:5, McNemar p=1.0), GSM8K +0.53pt (97.95 vs 97.42), AIME26 -0.84pt inside SEM ±1.67 (93.33 vs 94.17, n=8 protocol), thinking length per-task symmetric. Scale audit across 225M blocks explains why: the MSE sweep never selected a scale below the abs-max one (0.0% of blocks), matching abs-max on 54.8% and adding 1.5x headroom on 45% — on this family's weight distributions the sweep is headroom-only, so 4/6 degenerates to "abs-max plus safety margin" and the Flash-style harm mechanism (harder clipping) never activates.

Abs-max with 256 normalization (the `four_over_six` flag without the MSE sweep) is a third, distinct arm; label it as such in any A/B. Do not claim 4/6 benefit or harm by analogy across families; require a per-family paired A/B under the protocol in [validation-and-release.md](validation-and-release.md) showing non-inferiority on accuracy and thinking-length, and when available inspect the sweep's chosen-multiplier distribution (any mass below the abs-max scale is where the risk lives).

Model Optimizer ships the official implementation as `mtq.NVFP4_FOUR_OVER_SIX_CFG`, usable through `mtq.quantize` with HF or Megatron export. The config exists on main since before the reference commits above; release 0.46.0 is the first tag containing it. Sharp edges:

- **`four_over_six: true` inside a custom recipe's weight-quantizer cfg does NOT enable the adaptive selection.** The flag only switches E4M3 scale normalization from 448 to 256 (headroom for M=4 scales). The per-block M=4/M=6 choice is made by a weight-only MSE sweep over amax multipliers [1.0, 1.5] (`1.0` = map block max to 6, `1.5` = map to 4, lower reconstruction MSE wins). A recipe with `algorithm: method: max` plus the flag therefore produces plain abs-max quantization with 256-normalization — a valid checkpoint but NOT 4/6, and mislabeling it corrupts every downstream A/B. True 4/6 requires the `algorithm` block from the official preset `modelopt_recipes/configs/ptq/presets/model/nvfp4_four_over_six.yaml`: `method: mse`, `fp8_scale_sweep: false`, `start_multiplier: 1.0`, `stop_multiplier: 1.5`, `step_size: 0.5`. The sweep quantizes only weight tensors (original vs dequantized block MSE), so its result is independent of calibration data and sample count; the activation forward pass only feeds activation-side amax, which this recipe never bakes into the weight artifact.

- It is weight-side only; activations stay on ordinary dynamic NVFP4.
- `mtq.compress` does not preserve the per-block M=4/M=6 choice — export through `mtq.quantize` paths only.
- Do not combine it with second-order optimization recipes; the paper measured a 34.6% perplexity-gap increase with GPTQ.

Declare the choice in the conversion manifest as part of recipe identity. It complements, and does not replace, sensitive-layer exemption.

### Serving-Time Scale-Tensor Traps (Measured)

Two export-side scale contracts bite at serve time and are invisible to structural audits:

- **Attention k/v bmm scales are poison for sglang MLA.** An export that includes `model.layers.N.self_attn.k_proj.k_scale` / `v_proj.v_scale` (produced when the recipe enables `*[kv]_bmm_quantizer` for FP8 KV) gets silently hooked into the MLA attention path by sglang: the v-projection output is multiplied by `v_scale` (~2e-5), attention collapses, and generation degrades to repetition after the first token — with zero warnings and all structural audits green. The official `nvidia/GLM-5.2-NVFP4` checkpoint declares FP8 KV but ships **zero** such tensors. Contract: enable the bmm quantizers so `kv_cache_quant_algo: FP8` is declared, but **strip the `.k_scale`/`.v_scale` tensors at assembly** and treat their presence as an audit failure. (Evidence class: binary 0/5 → 5/5 serve battery with tensor presence as the only variable; official checkpoint serving healthy on the same runtime.)
- **Expert `input_scale` is `amax / (6 × 448)`, not an uncalibrated default.** On fused-experts models the shared per-layer-per-projection input quantizer collects a real activation amax during calibration and the export replicates it to every expert — identical values across the experts of a layer are by design. The official checkpoint ships `input_scale = 1.0` (the `constant_amax: 2688` = `6 × 448` contract; see ModelOpt's `nvfp4_experts_only_input_scale1-kv_fp8_cast` recipe). The degenerate ~1e-4 export has now been observed from two independent conversions (ModelOpt 0.46 and 0.47, `@use_experts_implementation` path) — it is a persistent upstream defect, not a one-off. sglang's `modelopt_fp4` cutedsl path is measured insensitive to the value (byte-identical output for ~1e-4 vs 1.0), but other runtimes may consume it asymmetrically — ship `1.0` unless you have a reason to keep the calibrated variant.

## Calibration Contract

Freeze before conversion:

- Dataset repository/revision/config/split or local content identity.
- Sample selection/order, sample count, tokenizer revision, sequence length, truncation/padding, batch size, seed, and calibration algorithm.
- Prompt/text extraction and preprocessing code identity.
- Architecture-specific forward kwargs.

Use production-representative text or activation data. Random Gaussian calibration is not a substitute — Gaussian input has produced 5x scale saturation with degenerate repetitive output while still passing every structural audit. Structure checks never prove calibration quality. For a new model family, first compare calibration activation ranges with representative real traffic, then gate the frozen pool with a probe: run a small target-distribution sample (for example GSM8K-style prompts) and require the probe activation amax to fall inside the calibration pool amax.

Architectures without a post-attention RMSNorm cannot synthesize calibration from embedding rows plus a norm; capture real activations from the serving path instead. See [whole-model-ptq.md](whole-model-ptq.md) for capture pitfalls.

A production whole-model reference that passed one dense hybrid Qwen-family conversion used 1,024 `cnn_dailymail` train samples, sequence length 512, batch size 1, seed 1234, and max calibration. This is evidence for that recipe and family, not a universal default.

The routed path's layer/expert calibration is defined separately because it cannot run the full model.

## Conversion Manifest

Canonicalize deterministic JSON and hash it before expensive work. `scripts/build_manifest.py` emits this shape; `manifest_sha256` is the SHA256 of the canonical JSON of every other field:

```json
{
  "architecture": "<preflight report: decision, decision_reason, layout, MTP and routed findings>",
  "arguments": "<normalized-object>",
  "calibration": "<normalized-object>",
  "conversion_artifacts": [{"name": "<path>", "sha256": "<sha256>", "size": 0}],
  "conversion_path": "<whole_model-or-routed_expert_streaming>",
  "environment": "<normalized-object>",
  "manifest_sha256": "<sha256>",
  "modelopt_commit": "<full-commit>",
  "precision_contract": "<normalized-object>",
  "recipe": {"name": "<name-or-path>", "path": "<resolved-file-if-any>", "sha256": "<sha256-if-file>"},
  "source": {
    "inventory": {"file_count": 0, "files": [{"name": "<path>", "sha256": "<sha256>", "size": 0}], "total_file_bytes": 0},
    "repository": "<repo-or-local-id>",
    "revision": "<immutable-revision>"
  },
  "topology": "<normalized-object>"
}
```

The path decision and its reason live in `architecture`; the environment is embedded whole rather than hashed separately. A recipe named by preset rather than by file records no `path`/`sha256`; the pinned `modelopt_commit` then identifies its body.

Store the manifest under a generation directory keyed by the full digest. Once conversion output exists, never edit it. Any output-affecting change creates a new generation.

`scripts/build_manifest.py` resolves the Model Optimizer checkout to an exact Git commit and requires it clean, hashes recipe and runner artifacts, rejects a non-executable preflight decision, and emits canonical manifest identity from normalized JSON inputs.

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
