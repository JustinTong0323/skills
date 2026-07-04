---
name: swe-bench
description: "Run SWE-bench Verified (the 500-instance coding benchmark) against any OpenAI-compatible endpoint. Use this skill whenever the user mentions SWE-bench, wants to benchmark a model on coding tasks, evaluate an LLM's code repair ability, run swebench verified, test a model served by SGLang or vLLM on software engineering tasks, or compare thinking vs non-thinking mode on SWE-bench. Also trigger when the user says 'run the benchmark', 'evaluate on swe-bench', 'test this endpoint on coding', or provides an API endpoint URL and asks to benchmark it."
version: 1.1.0
---

# SWE-bench Verified Runner

Run SWE-bench Verified against a local or remote OpenAI-compatible endpoint using mini-SWE-agent. The full pipeline: environment setup, config generation, Docker image pre-pull, agent execution (500 instances, parallelized), evaluation, and result reporting.

## Quick Reference

| Component | Tool | Purpose |
|-----------|------|---------|
| mini-swe-agent | Agent framework | Runs LLM against each instance in a Docker sandbox |
| swebench | Evaluation harness | Applies patches, runs tests, scores results |
| litellm | Model abstraction | Connects to any OpenAI-compatible endpoint via `hosted_vllm/` prefix |
| Docker | Sandboxing | Each instance runs in its own `swebench/sweb.eval.x86_64.*` container |

## Required Information

Gather these from the user (use AskUserQuestion if not provided):

1. **API base URL** (required) — e.g., `http://localhost:30000/v1`
2. **Model name** (required) — e.g., `moonshotai/Kimi-K2.5`, `Qwen/Qwen3-235B-A22B`
3. **Mode** — thinking (default) or non-thinking (`--no-think`)
4. **Workers** — parallel instances. **Set to ≤ 0.5 × server's `max-running-requests`** (see Phase 0). For typical sglang configs:
   - `max-running-requests=256` → workers `64` (safe) or `128` (max, risk SWA pressure on thinking)
   - `max-running-requests=128` → workers `32` (safe)
   - **Never** match workers to `max-running-requests` — server queue saturation collapses throughput.

Optional parameters: `--eval-only`, `--instances ID1,ID2,...`, `--skip-pull`, `--step-limit N` (default 250), `--timeout N` (bash command timeout, default 180s; **NOT** the API timeout — see Phase 0), `--container-timeout T` (default 12h).

## Phase 0: Server-side requirements for thinking-mode SWE-bench

Before benchmarking, verify the served model is launched with the right flags. Three things must be true on the server side or thinking-mode SWE-bench will degrade silently or hard-fail:

1. **Reasoning parser MUST be on** — without `--reasoning-parser <model>` (e.g. `deepseek-v4`, `qwen3`, `kimi-k2`), the `<think>...</think>` block leaks into `content`. The agent treats the leaked thinking as the actual response, gets confused, and most patches end up empty or wrong. Symptom: low resolve rate + many empty patches with reasoning text in `model_patch`.

2. **Tool-call parser MUST be on** — without `--tool-call-parser <model>` (e.g. `deepseekv4`, `qwen3`, `pythonic`), function-call syntax may not be extracted, the `bash` tool fires inconsistently, and you'll see `format_error_template` triggering frequently. Symptom: agent loops on "Tool call error" until step_limit.

3. **`reasoning_effort` schema is `low|medium|high` only** — sglang's OpenAI-compatible endpoint rejects `max`/`xhigh` (some upstream APIs accept `max`, sglang does not). For SWE-bench thinking, send **`reasoning_effort: high`**. Sending `max` returns 400.

4. **EAGLE / speculative decoding is HARMFUL for thinking-mode SWE-bench** — counter to the usual 1.5-2× decode speedup, EAGLE under multi-thousand-token reasoning contexts causes draft-token rejection cycles that churn the SWA cache and **collapse throughput by ~3.5×** (observed: 38 → 11 inst/hr on DSV4-Pro/GB300). Empirically: relaunch the server WITHOUT `--speculative-algo EAGLE` for SWE-bench thinking workloads.

5. **API client timeout ≥ 1200 s for thinking** — thinking outputs are 2-5× longer than instant outputs. The litellm completion timeout (NOT the bash `environment.timeout`) must be high enough or the client times out before generation finishes. Recommended:
   - Non-thinking: `timeout: 600`
   - Thinking, small/medium model: `timeout: 1200`
   - Thinking, large MoE (DSV4-Pro, K2.5, etc.): `timeout: 2400`

   Set this via `model.model_kwargs.timeout` in `sglang_swebench.yaml`.

6. **Do NOT manually set a small `--context-length`** — let sglang use the model's native `max_position_embeddings`. Thinking-mode turns are long (single responses routinely hit 60K+ tokens); a small `--context-length 32768` causes widespread `BadRequestError` (input + max_tokens > ctx) on long conversations. Manually setting it small is the single biggest source of false failures on thinking runs.

7. **`--thinking` flag is a no-op for `reasoning_effort`-based models** — `--thinking` injects `chat_template_kwargs={"thinking": true}`, which models like Hunyuan ignore (their chat template uses `reasoning_effort: high|low|no_think`, not `thinking`). On such models `--thinking` silently leaves the model in `no_think` and accuracy collapses. If the model uses `reasoning_effort`, set it explicitly in `model.model_kwargs` (see troubleshooting #12).

Verify on the server with:
```bash
curl -s "$API_BASE/models"  # should list the model id
# probe parsers — send a tiny chat with reasoning_effort=high and check
# that response.choices[0].message.reasoning_content is populated and content does NOT contain <think>
curl -s -X POST "$API_BASE/chat/completions" -H 'content-type: application/json' \
  -d '{"model":"MODEL","messages":[{"role":"user","content":"2+2?"}],"reasoning_effort":"high","max_tokens":256}' \
  | python3 -c 'import sys,json; r=json.load(sys.stdin)["choices"][0]["message"]; print("has_reasoning_content:", bool(r.get("reasoning_content"))); print("content_has_think:", "<think>" in (r.get("content") or ""))'
```

Expected: `has_reasoning_content: True` and `content_has_think: False`. If either is wrong, ask the user to relaunch the server with the correct parser flags.

## Pipeline

### Phase 1: Environment Setup

The workspace lives at `/root/swe-bench/`. Check if `.venv` exists; if not, bootstrap:

```bash
cd /root && mkdir -p swe-bench && cd swe-bench
export PATH="/root/.local/bin:$PATH"
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.10 .venv
source .venv/bin/activate
uv pip install mini-swe-agent swebench
```

System apt may be broken on some machines (held packages, missing python3.10-venv). Using `uv` avoids this entirely — it manages its own Python and doesn't touch system packages.

### Phase 2: Generate Configs

Read the bundled template scripts in this skill's `scripts/` directory. Use them to generate all config files, substituting the user's `api_base` and `model_name`.

Generate these files in `/root/swe-bench/`:

**registry.json** — Register the model with litellm at zero cost (critical for local endpoints):
```json
{
  "MODEL_NAME": {
    "max_tokens": 32768,
    "input_cost_per_token": 0.0,
    "output_cost_per_token": 0.0,
    "litellm_provider": "hosted_vllm",
    "mode": "chat"
  }
}
```

**sglang_swebench.yaml** — Read `scripts/sglang_swebench.yaml.template` from this skill directory. Copy it and replace the `model_name`, `api_base`, and timeout placeholders. Key settings that must be present:
- `model.cost_tracking: "ignore_errors"` — without this, zero-cost local models crash with "Cost must be > 0.0"
- `model.model_kwargs.timeout: 1200` — **litellm completion timeout** (NOT bash `environment.timeout`). For thinking workloads, set to 1200 s (or 2400 s for very large MoE models). Default 600 in template; bump for thinking.
- `model.model_kwargs.temperature` / `model.model_kwargs.max_tokens` / `model.model_kwargs.reasoning_effort` — these MUST live under `model.model_kwargs`, NOT under `model:` top level. mini-swe-agent 2.4.x ignores `model.temperature` / `model.max_tokens`; the model then runs at default temperature=0 even in "thinking" mode. (The thinking overlay sets `model_kwargs.temperature` correctly — keep all sampling knobs in `model_kwargs`.)
- `environment.timeout: 180` — bash command timeout per step (60s is too short, causes TimeoutExpired)
- `environment.container_timeout: "12h"` — Docker container lifetime (2h is too short, containers get killed mid-run)
- `agent.step_limit: 250` — max agent turns per instance

**sglang_swebench_thinking.yaml** — Thinking mode overlay (temperature 1.0)
**sglang_swebench_nothinking.yaml** — Non-thinking overlay (temperature 0.6, `chat_template_kwargs.thinking: false`)

**run.sh** — Read `scripts/run.sh.template`. Substitute model name. Make executable.
**eval.sh** — Read `scripts/eval.sh.template`. Substitute model name. Make executable.

All templates are in this skill's `scripts/` directory — read them to get the exact content.

### Phase 3: Verify Endpoint

```bash
curl -s API_BASE/models | python3 -c "import sys,json; m=json.load(sys.stdin); print([x['id'] for x in m['data']])"
```

If unreachable, tell the user and wait.

### Phase 4: Docker Image Pre-Pull

SWE-bench needs 500 Docker images (~50GB total). Pre-pulling avoids timeouts during the run. Skip if `--skip-pull` is set.

```bash
# Generate image list
python3 -c "
from datasets import load_dataset
ds = load_dataset('princeton-nlp/SWE-bench_Verified', split='test')
for row in ds:
    tag = row['instance_id'].replace('__', '_1776_')
    print(f'swebench/sweb.eval.x86_64.{tag}:latest')
" > all_images.txt

# Find missing
docker images --format '{{.Repository}}:{{.Tag}}' | sort > /tmp/local.txt
comm -23 <(sort all_images.txt) /tmp/local.txt > missing_images.txt

# Pull in parallel
xargs -a missing_images.txt -P 16 -I {} docker pull {}
```

This can take 30-60 minutes for a fresh pull. Report progress to the user.

### Phase 5: Run

```bash
cd /root/swe-bench
./run.sh [--no-think] --workers <N> [--filter '^(id1|id2)$']
```

**Pick `<N>` against the server's `max-running-requests`** (find it in the launch command):
- `max-running-requests=256`: start with `--workers 64`, can push to `128` if SWA pressure looks fine
- `max-running-requests=128`: start with `--workers 32`, do NOT exceed `64`
- Bare-metal H200 with `tp=8 dp=8` and FP8 model: `--workers 128` is reproducibly safe
- GB300 with `--moe-a2a-backend deepep` and any thinking-mode model: **start with `--workers 32`** — large MoE + DeepEP + thinking creates heavy SWA pressure, and `--workers 128` has produced 0 completions in 1h before being killed.

For `--instances`, convert comma-separated IDs to a `--filter` regex.

Monitor with: `docker ps -q | wc -l` (running containers) and `ls results/<mode>/ | wc -l` (completed trajectories).

**Watch for SWA collapse**: if `docker ps -q | wc -l` stays high (= the workers count) for >30 min and `ls results/<mode>/ | wc -l` doesn't grow, the server's KV cache is saturated and every in-flight request is timing out. Kill the run, halve workers, restart. See troubleshooting entry "Queue collapse / SWA saturation".

### Phase 6: Evaluate

Evaluation also needs Docker — it spins up containers to apply patches and run test suites.

**Before running eval after a partial rerun**: the harness skips instances that already have eval logs. If you used `--redo-existing` (or any rerun) you MUST first delete stale eval logs or it will score the OLD patches:
```bash
rm -rf logs/run_evaluation/<run_id>/      # run_id is the -id arg to eval.sh, e.g. think_hy3
```
Symptom if you forget: the summary JSON's `resolved_instances` won't match a per-instance report.json aggregation (one is from the prior run, one from the rerun). Always reconcile the two — a mismatch means stale eval.

```bash
./eval.sh <think|non-think> [max_workers]
```

`max_workers` defaults to `16` in the template (much faster than the upstream-default `5`). On a many-core host (≥64 cores) bump to `32`; on `ion-h200-8` (192 cores) `32` cuts eval wall-clock from ~80 min → ~20 min for a 500-instance run. **Start at `16`** then bump up if `docker ps -q | wc -l` shows headroom.

**Report location & aggregation**: the per-instance `report.json` files live under `logs/run_evaluation/<run_id>/hosted_vllm__<model>/*/report.json` (NOT in `results/`). There is no top-level summary file on some versions — aggregate yourself:
```python
import json, glob
fs = glob.glob('logs/run_evaluation/*/hosted_vllm_*/*/report.json')
resolved = sum(1 for f in fs for iid, info in json.load(open(f)).items() if info.get("resolved"))
print(f"Resolved: {resolved}/{len(fs)} = {100*resolved/len(fs):.1f}%")
```
(The `-id` arg to `eval.sh` becomes the `<run_id>` segment; `report.json` is keyed by instance_id with a `"resolved"` bool.)

### Phase 7: Report

Parse and display:
```python
import json
r = json.load(open('REPORT_FILE'))
print(f"Resolved: {r['resolved_instances']}/{r['total_instances']} ({100*r['resolved_instances']/r['total_instances']:.1f}%)")
```

Then ask the user if they want to:
1. Run the other mode (think vs non-think)
2. Push results to a GitHub repo
3. Generate a detailed analysis report comparing modes
4. **Run a Pass@k ensemble** (see Phase 8)

### Phase 8: Pass@k ensemble (recommended for ceiling-pushing)

Single-run results have intrinsic stochastic variance under `temperature=1.0`. Three independent SWE-bench runs of the DSV4 family all landed at exactly **368/500 = 73.60 %**, but the resolved instance sets differed substantially:

| Comparison | |intersection| | sym-diff |
|---|---|---|
| Run 1 vs Run 2 | 344 | 48 |
| Run 1 vs Run 3 | 344 | 48 |
| Run 2 vs Run 3 | 341 | 54 |

→ Pass@3 union = **403/500 = 80.60 %** (+7 pp over any single run).

**This is the cheapest +pp scaling lever on SWE-bench Verified** — far cheaper than upgrading model size (DSV4-Pro 1.6T provided **zero** quality lift over Flash 284B on the same benchmark, at 3.7× the wall-clock).

To run a Pass@k ensemble:
1. Run the benchmark `k` times with different `output_dir` suffixes:
   ```bash
   ./run.sh --workers 64                                  # produces results/think/
   mv results/think results/think_run1
   ./run.sh --workers 64                                  # results/think (run 2)
   mv results/think results/think_run2
   # ... etc
   ```
   (Or run `k` parallel benchmarks against `k` different endpoints if you have them — much faster.)
2. Run `./eval.sh` on each `results/think_runN/`, producing `k` report JSONs.
3. Use the Pass@k script:
   ```bash
   python3 /path/to/skill/scripts/parse_results.py report1.json report2.json report3.json
   ```
   Output includes per-run scores, pairwise overlap matrix, and the Pass@k union score.

Caveat: if 2+ runs are on the **same** model + same temperature + same prompt, they share the same modal answer for ~328 instances and you only get diversity on ~75 swing instances. For a bigger Pass@k lift, vary either temperature, sampling strategy, or use distinct model checkpoints.

## Troubleshooting

Read `references/troubleshooting.md` for the full list of known issues and fixes. The most critical ones:

- **"Cost must be > 0.0"** — Add `cost_tracking: "ignore_errors"` to config
- **"Skipping N existing instances"** — Delete `exit_statuses_*.yaml` and clean empty entries from `preds.json`
- **Docker exit code 125** — Reduce workers, pre-pull images
- **Agent hangs at high step counts** — Container was killed; increase `container_timeout`
- **Reasoning text leaks into `model_patch`** — Server is missing `--reasoning-parser`. Relaunch with the right parser.
- **HTTP 400 on `reasoning_effort: max`** — sglang only accepts `low|medium|high`. Use `high`.
- **0 completions in 1h with workers near max-running-requests** — Server queue saturated. Halve workers and restart.
- **38 → 11 inst/hr after enabling EAGLE specdec** — EAGLE is harmful for thinking-mode. Drop `--speculative-algo` and relaunch.
- **Litellm `Timeout` errors mid-instance** — `model.model_kwargs.timeout` is too low. Bump to 1200 (or 2400 for big MoE thinking).
