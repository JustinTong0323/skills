# Streaming And Assembly

## Portable Paths

Define site-local values outside shared source control:

```bash
export CONVERSION_ROOT="<workspace>"
export MODELOPT_ROOT="<modelopt-checkout>"
export SOURCE_CHECKPOINT="<read-only-bf16-checkpoint>"
export REFERENCE_CONFIG="<compatible-quantized-config-json>"
export STAGING_CHECKPOINT="<staging-directory>"
export OUTPUT_CHECKPOINT="<final-output-directory>"
export PYTHON_BIN="<isolated-python>"
```

Require:

- `SOURCE_CHECKPOINT` contains a valid config and safetensors index.
- `OUTPUT_CHECKPOINT` does not exist.
- Staging and output are on the same filesystem.
- The canonical source remains read-only.
- Available capacity covers routed parts, assembly output, temporary export, and filesystem headers.

Do not mix tensor payload bytes from index metadata with on-disk safetensors file bytes when planning capacity.

## Environment Preparation

```bash
git clone https://github.com/NVIDIA/Model-Optimizer.git "$MODELOPT_ROOT"
git -C "$MODELOPT_ROOT" checkout 87c9f8cf83021957d1a1a575c90c9a4eaaf7ef0c

python3 -m venv --system-site-packages "$CONVERSION_ROOT/venv"
export PYTHON_BIN="$CONVERSION_ROOT/venv/bin/python"

"$PYTHON_BIN" -m pip install --upgrade \
  "pip>=25" "setuptools>=80" "setuptools-scm>=8,<10"
"$PYTHON_BIN" -m pip install --no-deps -e "$MODELOPT_ROOT"
"$PYTHON_BIN" -m pip install \
  "accelerate>=1.0.0" \
  "datasets>=3.0.0" \
  "omegaconf>=2.3.0" \
  "pulp<4.0" \
  "pydantic>=2.0" \
  rich safetensors \
  "sentencepiece>=0.2.1"
```

Run every conversion command with `PYTHONNOUSERSITE=1`, `TOKENIZERS_PARALLELISM=false`, and `PYTHONPATH` pointing at the pinned Model Optimizer checkout.

## Metadata Preflight

The preflight tool must use config, index, and `safe_open(...).get_slice()` metadata. It must not materialize the full checkpoint.

For every layer, verify the fused source shapes:

```text
gate_up_proj: [num_experts, 2 * moe_intermediate_size, hidden_size]
down_proj:    [num_experts, hidden_size, moe_intermediate_size]
```

Require BF16 input tensors, record their source shard names, enumerate the complete non-routed key set, and verify all expected MTP keys are outside the routed set.

## Real-Weight Dry Run

The dry run uses exactly one layer and one expert. It must execute the same load, calibration, quantization, export, renaming, tensor validation, and marker code used by the full workers.

```bash
env \
  PYTHONNOUSERSITE=1 \
  PYTHONPATH="$MODELOPT_ROOT" \
  TOKENIZERS_PARALLELISM=false \
  "$PYTHON_BIN" <STREAMING_EXPORTER> \
    --source "$SOURCE_CHECKPOINT" \
    --reference-config "$REFERENCE_CONFIG" \
    --staging "$STAGING_CHECKPOINT" \
    --layers 0 \
    --expert-range 0:1 \
    --dry-run
```

Record the dry-run part and marker hashes. The full coordinator must require that marker's source, Model Optimizer, recipe, and calibration identities.

## Full Conversion

Launch four independent processes with one GPU per process. Scheduler syntax is site-specific; the effective contract is:

```text
nodes:                 1
processes:             4
GPUs per process:      1
partition axis:        expert
layers per iteration:  1
collectives:           none
failure policy:        terminate all workers when one worker fails
```

Each worker derives its partition index from the process rank. For every layer it must:

1. Construct a one-layer model containing only its expert range.
2. Open only the source shards required for that layer.
3. Slice assigned `gate_up_proj` and `down_proj` experts on CPU.
4. Move only those weights and representative calibration state to its GPU.
5. Apply `NVFP4_EXPERTS_ONLY_CFG` with `*mtp*` disabled.
6. Run deterministic calibration and require finite slice output.
7. Call `export_hf_checkpoint` into a unique temporary directory.
8. Extract routed tensors and restore global layer and expert names.
9. Validate exact tensor coverage, shapes, dtypes, and positive scales.
10. Atomically publish the immutable part and marker.
11. Delete the model, batches, tensors, and export directory; run garbage collection and empty the CUDA allocator cache.

One worker must never retain allocations from an earlier layer while starting the next. GPU memory growth across layers is a lifecycle bug, not a reason to add ranks.

## Supervision And Resume

For each layer, require all four disjoint marker ranges before advancing or releasing staged source shards. Track:

- Worker PID, rank, GPU, current layer, and expert range.
- Exit code and last successful marker per worker.
- GPU memory and utilization.
- Host/cgroup memory and OOM counters.
- Staging and source capacity.
- Part count, marker count, byte growth, and latest verified completion time.

On restart, recompute the expected identity. Reuse a part only when the complete marker identity, part size, and part SHA256 match. Move stale or partial files to quarantine rather than overwriting evidence.

## CPU Assembly

Assembly does not require a GPU, but it is storage and host-memory intensive.

```bash
env \
  PYTHONNOUSERSITE=1 \
  PYTHONUNBUFFERED=1 \
  "$PYTHON_BIN" <ASSEMBLER> \
    --source "$SOURCE_CHECKPOINT" \
    --reference-config "$REFERENCE_CONFIG" \
    --staging "$STAGING_CHECKPOINT" \
    --output "$OUTPUT_CHECKPOINT" \
    --mtp-policy keep-bf16
```

The assembler must:

1. Re-hash and inspect every routed part and marker.
2. Reject duplicate, missing, overlapping, or out-of-range global tensor keys.
3. Exclude the original fused BF16 routed tensors only after every replacement is verified.
4. Copy every other source tensor unchanged, including MTP.
5. Preserve auxiliary tokenizer and remote-code files without copying source safetensors or the old index.
6. Merge the reference and exported ignore lists with all MTP exclusions.
7. Remove KV-cache quantization fields from both modern and legacy quantization config forms.
8. Write a unique complete safetensors index and correct tensor-payload byte total.
9. Validate the staging checkpoint before publication.
10. Atomically rename staging to the final output on the same filesystem.

If the target loader ignores nested paths in the index, create collision-checked root-level hard links and atomically rewrite the index to basenames. Verify inode identity before the rewrite. Do not duplicate terabytes of payload merely to change layout.

## Common Multi-Node Loading Pitfalls

These were each observed to block a two-node TP8 launch or to silently corrupt its shard coverage. Check them before blaming NCCL or the network:

- `LOCAL_SIZE` must reflect the per-node rank count, not global TP. Some SGLang revisions overwrite an externally supplied `LOCAL_SIZE` with global TP, causing each node to prefetch only `TP_local / TP_global` of the total shards. On a 2-node TP8 launch this made each node fetch only half of the shards and the load appeared to hang while ranks on the other node were missing data.
- Checkpoint prefetch threads must fit inside the host page cache. On one node, 4 prefetch threads per local rank outran the available page cache and thrashed I/O; one thread per local rank was sufficient. Measure page-cache headroom, do not guess.
- A long runner-rank imbalance is not the same as a deadlock. When one node reads from local NVMe and the other from a slow shared filesystem, per-rank load completion can differ by tens of minutes. Raise the post-load barrier (for SGLang, `SGLANG_UNBALANCED_MODEL_LOADING_TIMEOUT_S`) high enough for the slow node; do not kill the fast ranks.
- Verify each rank actually loaded its shard set. Rank-0 health alone cannot prove the other 7 ranks finished.
