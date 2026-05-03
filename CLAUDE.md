# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workspace Layout

This workspace contains multiple sglang checkouts for parallel feature development:

- `sglang-main/` — main sglang checkout (upstream: sgl-project/sglang)
- `gemma4/sglang-gemma4/` — Gemma4 model support branch
- `gemma4/sglang-pr/` — PR validation checkout
- `whisper-cudagraph/sglang/` — Whisper CUDA graph optimization branch
- `upgrade-transformers/sglang/` — Transformers 5.4.0 upgrade branch
- `kimi-toolcall/sglang/` — Kimi-2.5 tool call constrained decoding branch
- `mm-attn-default/sglang/` — MM attention default FA4 branch

All share the same codebase structure. The primary working copy is `sglang-main/`.

**Cookbook has moved**: `sgl-cookbook/` on disk is the legacy standalone repo (read-only / archival). New cookbook content goes in `sglang-main/docs_new/` (same PR workflow as regular sglang changes). Do not edit `/root/xinyuan/workspace/sgl-cookbook/` for new docs — any PR there targets a deprecated repo.

### Worktree Convention

All worktrees are created from the main `sglang-main/` repo. Each feature gets a top-level directory (e.g., `feature-name/sglang/`) via:
```bash
cd sglang-main && git worktree add /root/xinyuan/workspace/<feature-name>/sglang -b <branch-name>
```
Use `git worktree list` in `sglang-main/` to see all active worktrees.

**Preflight before editing any file**: confirm `pwd` is in a feature worktree (`/root/xinyuan/workspace/<feature>/sglang` or similar). Do not edit files directly under `sglang-main/` or legacy `sgl-cookbook/` — create or switch to a worktree first. The session's initial cwd is not reliable.

**Each worktree needs its own venv** — never `source sglang-main/.venv` from another worktree, and never run tests against the main venv from inside a worktree:
```bash
cd <worktree>/sglang && uv venv --python 3.12 && source .venv/bin/activate && uv pip install -e "python[test]"
```
Fresh `uv venv` without `--python 3.12` will pick up 3.13 and break the editable install.

### Git Remotes (in sglang-main/)

- `origin` — `JustinTong0323/sglang` (fork)
- `upstream` — `sgl-project/sglang` (upstream)
- `gemma4` — `pyc96/sglang-private`

## Remote Dev Utils Kit

This workspace includes a local remote-development kit at `./remote_dev_utils/`.
Use it when working with remote Kubernetes GPU pods, Radixark GB200/GB300/B200/H200
machines, or SWE-bench runs against OpenAI-compatible endpoints.

The kit is not a standalone CLI. It contains reusable skill docs plus a kubeconfig:

- `remote_dev_utils/skills/remote-k8s-dev/SKILL.md` - generic k8s pod workflow:
  create/delete pods, copy patches, start SGLang servers with `nohup`, poll health,
  run benchmarks, port-forward, collect logs/results, and clean up.
- `remote_dev_utils/skills/gb200-cluster-guide/SKILL.md` - Radixark cluster facts:
  GB300 GKE topology, B200/H200 bare-metal hosts, NFS layout, pod YAML requirements,
  image choices, and known GB300/Kimi-K2.5 quirks.
- `remote_dev_utils/skills/swe-bench/SKILL.md` - SWE-bench Verified runner:
  endpoint checks, config generation, Docker image pre-pull, mini-SWE-agent run,
  evaluation, and result parsing.
- `remote_dev_utils/gcp_radixark_02_kube_config.yaml` - kubeconfig for the
  `gcp-radixark-02` cluster. Use it with:
  ```bash
  export KUBECONFIG=/root/xinyuan/workspace/remote_dev_utils/gcp_radixark_02_kube_config.yaml
  kubectl config get-contexts
  kubectl get pods -A -o wide
  ```

## Build & Install

The main sglang checkout uses a **uv-managed virtual environment** at `sglang-main/.venv` (Python 3.12).

```bash
# Create venv and install (typical workflow)
cd sglang-main && uv venv && uv pip install -e python

# With diffusion support
uv pip install -e "python[diffusion]"

# Activate the venv (if not already)
source .venv/bin/activate

# sgl-kernel (CUDA kernels)
cd sgl-kernel && make build          # build wheel
cd sgl-kernel && make install        # dev install
cd sgl-kernel && make build MAX_JOBS=2  # limit parallelism
cd sgl-kernel && make rebuild        # clean + rebuild
```

The `pyproject.toml` uses `[tool.uv.index]` and `[tool.uv.sources]` to configure PyTorch index URLs (cu129 for aarch64). CI scripts also use `uv pip` as the default package installer.

## Testing

### CI Operations

- Rerun failed CI: comment `/rerun-failed-ci` on the PR (preferred over `gh run rerun`). For branches needing CI retrigger + tag (e.g. transformers upgrade PRs), use `/tag-and-rerun-ci` instead
- Before rerunning: cancel active workflows first with `gh run cancel <RUN_ID> --repo sgl-project/sglang`, then comment `/rerun-failed-ci`
- **Never `gh run rerun`** — it passes pr-gate and burns the PR's 120-min pr-gate cooldown; the cooldown logic lives inside the pr-gate job script, not the workflow yaml, so it's invisible until you're stuck waiting
- CI uses fast-fail: if a root-cause job fails, all downstream stage-b jobs fail instantly (~10s) with "Fast-fail: skipping". Always find the root-cause job in logs before deciding flaky vs real
- Monitor CI with `CronCreate` polling every 20min, not background shell scripts
- Wait for server startup / long-queued jobs with the `Monitor` tool — never `sleep N && tail`, `until grep -q ...; do sleep ...; done`, `for i in 1..N; do sleep ...; done`, blocking `sglang.launch_server` in foreground, or `while curl` loops. Any handwritten poll loop will be interrupted; use Monitor on the log file or job
- `gh pr checks` can miss stage-c jobs — use `gh pr view <PR> --repo sgl-project/sglang --json statusCheckRollup` for complete status
- Check failure annotations: `gh api repos/sgl-project/sglang/check-runs/<JOB_ID>/annotations --jq '.[].message'`
- File-anchored review comments: `gh api repos/sgl-project/sglang/pulls/<n>/comments -f path=... -F line=... -f side=RIGHT -f body=...` — `gh pr review --comment` only posts unanchored review-level comments
- Common infra failures: exit 128 (runner killed), "Fast-fail: skipping" (cascade). These need `/rerun-failed-ci`, not code fixes
- **IMPORTANT**: exit 255 does NOT always mean infra failure — it is the CI runner's wrapper exit code when a test file returns exit code 1. Always check the actual job logs (`gh api repos/sgl-project/sglang/actions/jobs/<JOB_ID>/logs`) to distinguish real test failures from runner crashes. Look for `FAILED:` lines to find which test file failed and the actual traceback

```bash
# Run a single test file
python3 test/registered/core/test_srt_endpoint.py

# Run a single test method
python3 test/registered/core/test_srt_endpoint.py TestSRTEndpoint.test_simple_decode

# Run a CI suite locally
python3 test/run_suite.py --hw cpu --suite stage-a-test-cpu
python3 test/run_suite.py --hw cuda --suite stage-b-test-1-gpu-small

# sgl-kernel tests
cd sgl-kernel && pytest tests/

# JIT kernel tests live in python/sglang/jit_kernel/tests/
```

### Test Conventions

- Always extend `CustomTestCase` (from `sglang.test.test_utils`), never raw `unittest.TestCase`
- Register tests at module level with `register_cuda_ci(est_time=N, suite="...")` or `register_cpu_ci(...)` — these must use **literal values** (AST-parsed by `test/run_suite.py`)
- Use `popen_launch_server()` for integration tests; always `kill_process_tree()` in `tearDownClass`
- Default models: `meta-llama/Llama-3.2-1B-Instruct` (small), `meta-llama/Llama-3.1-8B-Instruct` (large)
- Reuse one server per test class rather than launching per-test
- Read `test/README.md` and `test/registered/README.md` for full CI suite info

## Linting & Formatting

```bash
# Run all pre-commit hooks
pre-commit run --all-files

# sgl-kernel formatting
cd sgl-kernel && make format
```

Tools: **isort** (imports), **black** (Python), **ruff** (F401/F821 only), **clang-format** (C++/CUDA), **codespell** (spelling)

**Linting gotchas:**
- ruff F401 removes "unused" imports — deferred re-exports (e.g. `from .sub import x as _x` used by sibling modules) need `# noqa: F401`
- Always run `pre-commit run --all-files` twice — first run auto-fixes, second confirms clean

## SGLang Architecture

### Two Subsystems

1. **SRT (SGLang Runtime)** — `python/sglang/srt/` — LLM serving engine
2. **Diffusion** — `python/sglang/multimodal_gen/` — image/video generation (has its own `.claude/CLAUDE.md`)

Plus a **frontend language** (`python/sglang/lang/`) and **CLI** (`python/sglang/cli/`).

### SRT Request Lifecycle

```
HTTP (FastAPI) → TokenizerManager (tokenize, ZMQ send)
  → Scheduler subprocess (event loop: recv → batch → forward → process results)
    → ModelRunner (GPU forward pass)
  → DetokenizerManager subprocess (decode tokens → text)
  → HTTP response (streamed)
```

### Key SRT Components

| Component | Location | Role |
|-----------|----------|------|
| Engine | `srt/entrypoints/engine.py` | Main entry, launches subprocesses |
| HTTP Server | `srt/entrypoints/http_server.py` | FastAPI, OpenAI-compatible API |
| Scheduler | `srt/managers/scheduler.py` | Event loop: batching, scheduling, GPU dispatch |
| ModelRunner | `srt/model_executor/model_runner.py` | GPU forward execution |
| ServerArgs | `srt/server_args.py` | All config params (285KB, 100+ options) |
| ScheduleBatch/Req | `srt/managers/schedule_batch.py` | Request and batch data structures |
| RadixCache | `srt/mem_cache/radix_cache.py` | Prefix KV cache sharing (trie) |
| MemoryPool | `srt/mem_cache/memory_pool.py` | Token-to-KV cache allocation |

### Scheduler Event Loop

The Scheduler is the central orchestrator — a single-threaded loop per GPU:
1. `recv_requests()` — receive from TokenizerManager via ZMQ
2. `process_input_requests()` — add to waiting queue
3. `get_next_batch_to_run()` — schedule policy picks batch (prefill vs decode)
4. `run_batch()` — GPU forward via ModelRunner
5. `process_batch_result()` — sample tokens, update KV cache, check completion

Two modes: **normal** (sequential) and **overlap** (CPU/GPU pipelined).

### Multi-GPU

- **Tensor Parallelism (TP)**: model layers sharded across GPUs
- **Pipeline Parallelism (PP)**: layers split vertically across stages
- **Data Parallelism (DP)**: different requests on different GPUs
- **Sequence Parallelism (SP)**: for diffusion (ulysses/ring attention)
- Distributed state in `srt/distributed/parallel_state.py`

### sgl-kernel (CUDA Kernels)

Located in `sgl-kernel/`. C++/CUDA kernels built with CMake + scikit-build-core.

```
sgl-kernel/
├── csrc/          # C++/CUDA sources (attention, gemm, moe, quantization, etc.)
├── include/       # Public headers (sgl_kernel_ops.h)
├── python/sgl_kernel/  # Python bindings
├── tests/         # Unit tests
└── benchmark/     # Performance benchmarks
```

To add a kernel: implement in `csrc/` → expose in `sgl_kernel_ops.h` → register in `common_extension.cc` → update `CMakeLists.txt` → add Python wrapper in `python/sgl_kernel/`.

### JIT Kernels

Triton-based runtime-compiled kernels in `python/sglang/jit_kernel/` — flash attention, RoPE, quantization, normalization, etc.

## CLI Entry Points

```bash
sglang serve --model-path <model>    # Launch server
sglang generate --model-path <model> --prompt "..."  # One-shot (diffusion)
```

## Key Environment Variables

Defined in `python/sglang/srt/utils.py` and `python/sglang/multimodal_gen/envs.py`:
- `SGLANG_IS_IN_CI=true` — CI mode
- `SGLANG_DIFFUSION_ATTENTION_BACKEND` — diffusion attention override
- `SGLANG_CACHE_DIT_ENABLED` — Cache-DiT acceleration

## B200 (sm_100) Notes
- sgl-kernel requires specific torch version (check `pip show sgl-kernel` for compatibility)
- vLLM FA4 on B200 requires `nvidia-cutlass-dsl` with `.pth` file; if missing: `pip install --force-reinstall nvidia-cutlass-dsl==4.4.2`
- Switching between sglang (torch 2.9.1) and vllm (torch 2.10.0) benchmarks requires `pip install torch==<version>`

## ASR Benchmarking
- Script: `benchmark/asr/bench_sglang.py` — works for both sglang and vllm via OpenAI-compatible `/v1/audio/transcriptions` API
- Deps: `pip install librosa jiwer evaluate`
- Default dataset: `D4nt3/esb-datasets-earnings22-validation-tiny-filtered` (511 samples)
