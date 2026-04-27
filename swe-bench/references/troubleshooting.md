# SWE-bench Troubleshooting Guide

## Common Issues

### 1. Cost Tracking Error

**Symptom**: `ValueError: Cost must be > 0.0, got 0.0`

**Cause**: litellm enforces positive costs by default. Local models have zero cost.

**Fix**: Add to `sglang_swebench.yaml`:
```yaml
model:
  cost_tracking: "ignore_errors"
```

### 2. Skipping Existing Instances

**Symptom**: `Skipping N existing instances` — mini-swe-agent thinks instances are already done.

**Cause**: Three things mark an instance as "done":
- `exit_statuses_*.yaml` files (completion tracker)
- Entries in `preds.json` (even with empty patches)
- Trajectory directories

**Fix**: Run the bundled cleanup script:
```bash
python3 /path/to/skill/scripts/clean_results.py results/<mode>/
```

Or manually:
```bash
# Delete exit status files
rm -f results/<mode>/exit_statuses_*.yaml

# Filter preds.json to keep only good entries
python3 -c "
import json
data = json.load(open('results/<mode>/preds.json'))
good = {k:v for k,v in data.items() if v.get('model_patch','').strip()}
json.dump(good, open('results/<mode>/preds.json','w'), indent=2)
print(f'Kept {len(good)}/{len(data)}')
"
```

### 3. Docker Overwhelmed

**Symptom**: `CalledProcessError`, exit code 125, containers failing to start.

**Cause**: Too many concurrent containers exhausting Docker daemon resources.

**Fix**:
1. Reduce `--workers` (64 is usually safe, 256 is too many)
2. Pre-pull all images first (avoids pull-during-run bottleneck)
3. Check Docker resource limits: `docker info | grep -i memory`

### 4. Command Timeout

**Symptom**: `TimeoutExpired` errors, often at 60 or 130 seconds.

**Cause**: The `environment.timeout` in config is too short for complex commands (installing deps, running test suites).

**Fix**: Set `timeout: 180` (or higher) in `sglang_swebench.yaml`:
```yaml
environment:
  timeout: 180
```

### 5. Container Killed Mid-Run

**Symptom**: Agent reaches step 30+ but Docker container disappears. Agent hangs or errors.

**Cause**: `container_timeout` (default "2h") expires. The Docker container has a `sleep` command that keeps it alive — when it expires, the container is removed via `--rm`.

**Fix**: Increase `container_timeout`:
```yaml
environment:
  container_timeout: "12h"
```

Containers are cleaned up after each instance completes (the `cleanup()` method in `docker.py` stops and removes them), so long timeouts don't waste resources.

### 6. Eval Report in Wrong Location

**Symptom**: Can't find the evaluation report JSON after `./eval.sh`.

**Cause**: `swebench.harness.run_evaluation` writes the report to the current working directory, not necessarily the `--report_dir`.

**Fix**: Check both locations:
```bash
ls /root/swe-bench/hosted_vllm__*.json
ls results/<mode>/*.json
```

The filename pattern is: `hosted_vllm__<model_name_with_underscores>.<run_id>.json`

### 7. Think Mode Timeouts/LimitsExceeded

**Symptom**: Think mode produces empty patches with exit status `Timeout` or `LimitsExceeded`.

**Cause**: Extended thinking tokens consume more API time and context. ~1.8% of instances may hit limits.

**Fix**: This is somewhat inherent to thinking mode. Options:
- Accept the ~2% empty patch rate
- Rerun failed instances with non-think mode as fallback
- Increase API timeout if possible

### 8. Docker Image Naming

The image name for an instance ID is derived by replacing `__` with `_1776_`:
```
Instance: django__django-10554
Image:    swebench/sweb.eval.x86_64.django_1776_django-10554:latest
```

### 9. litellm Provider Prefix

For SGLang/vLLM endpoints, the model name in config must have the `hosted_vllm/` prefix:
```yaml
model:
  model_name: "hosted_vllm/moonshotai/Kimi-K2.5"
```

But the registry.json key does NOT have the prefix:
```json
{
  "moonshotai/Kimi-K2.5": { ... }
}
```

### 10. Broken System Python/apt

**Symptom**: `apt install python3.10-venv` fails with held packages or dependency conflicts.

**Fix**: Use `uv` instead of system Python:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.10 .venv
```

`uv` manages its own Python installations and doesn't touch system packages.

### 11. Reasoning text leaks into `content` / `model_patch`

**Symptom**: The agent's `content` starts with `<think>...</think>` blocks, OR the final patch contains reasoning prose instead of a diff. Resolve rate is anomalously low (<30 %) even on a strong model.

**Cause**: Server was launched without `--reasoning-parser`. The OpenAI-compatible endpoint emits the entire generation (reasoning + answer) into `choices[0].message.content` instead of splitting into `reasoning_content` + `content`.

**Fix**: Relaunch the server with the correct parser flag. Example for DeepSeek-V4:
```bash
python3 -m sglang.launch_server ... \
  --reasoning-parser deepseek-v4 \
  --tool-call-parser deepseekv4
```
For other models, the parser name follows the model family: `qwen3`, `kimi-k2`, `glm-4`, etc. Confirm with the probe in Phase 0.

### 12. `reasoning_effort: max` rejected with 400

**Symptom**: `litellm.BadRequestError: ... reasoning_effort must be one of {'low', 'medium', 'high'}`

**Cause**: sglang's OpenAI-compatible schema is stricter than DeepSeek's official API. `max` and `xhigh` are NOT accepted.

**Fix**: Use `reasoning_effort: high` (mapped from DeepSeek's `low|medium|high → high`, `xhigh → max` — but sglang treats `high` as the maximum effort).

In `sglang_swebench_thinking.yaml`:
```yaml
model:
  model_kwargs:
    extra_body:
      reasoning_effort: "high"
```

### 13. Queue collapse / SWA cache saturation under high concurrency

**Symptom**: After ~5-30 min of healthy progress, `ls results/<mode>/ | wc -l` stops growing while `docker ps -q | wc -l` stays pinned at the worker count. Server logs show generation slowing to <1 tok/s/request, every in-flight request eventually times out, retries pile up, the run produces 0 useful completions per hour.

**Cause**: Client `--workers` matches or exceeds server's `--max-running-requests`. Under thinking-mode, multi-thousand-token outputs starve the SWA (sliding-window attention) KV pool. Once saturated, no request can advance, but the queue keeps adding new requests as old ones time out and retry.

**Fix**: Halve workers and restart. Rule of thumb: `workers ≤ 0.5 × max-running-requests`. For DSV4-Pro on 4×GB300 (`max-running-requests=128`), `--workers 32` is the validated value; `--workers 128` produced 0/500 in 1 h.

Diagnostic script:
```bash
# while run is in progress
docker ps -q | wc -l                              # in-flight (should be ≈ workers)
ls results/<mode>/*/ -d 2>/dev/null | wc -l       # completed (should grow steadily)
# if first stays high and second is flat for >20min → saturation
```

### 14. EAGLE / speculative decoding harms thinking-mode SWE-bench throughput

**Symptom**: Throughput collapsed from ~38 inst/hr (no EAGLE) to ~11 inst/hr (with EAGLE), despite EAGLE normally being a 1.5-2× decode speedup on instant workloads.

**Cause**: Under multi-thousand-token reasoning chains, EAGLE's draft-token rejection rate spikes. Each rejection forces a KV recompute and SWA pool churn. The aggregate effect is a ~3.5× slowdown on thinking SWE-bench (validated on DSV4-Pro/GB300).

**Fix**: Relaunch the server WITHOUT speculative decoding for SWE-bench thinking workloads. Drop these flags:
```
--speculative-algo EAGLE
--speculative-num-steps N
--speculative-eagle-topk N
--speculative-num-draft-tokens N
```

EAGLE is fine for non-thinking SWE-bench (instant decode is the baseline EAGLE was designed for) and for non-SWE-bench workloads. The harm is specific to long-reasoning + agent-loop pattern.

### 15. Litellm `Timeout` errors mid-instance with thinking models

**Symptom**: `litellm.Timeout: Connection timed out` after exactly 600s on most thinking-mode requests.

**Cause**: The default `timeout` in `model.model_kwargs` is too low for thinking outputs. Thinking generations are 2-5× longer than instant.

**Fix**: Set `timeout` in the yaml:
```yaml
model:
  model_kwargs:
    api_base: "..."
    timeout: 1200      # seconds. 1200 for medium thinking models, 2400 for large MoE.
    drop_params: true
    parallel_tool_calls: true
```

Recommended timeouts:
| Workload | timeout |
|---|---|
| Non-thinking | 600 |
| Thinking, dense ≤70B | 1200 |
| Thinking, MoE 100-300B | 1200 |
| Thinking, MoE 1T+ (DSV4-Pro, K2.5) | 2400 |

Note: this is the LITELLM completion timeout, NOT the bash `environment.timeout` (180s). The two are independent and both are needed.

### 16. Eval `max_workers=5` is timid on a many-core host

**Symptom**: `swebench.harness.run_evaluation` takes ~80 min on a 500-instance run when host has spare CPU + I/O.

**Cause**: Upstream-default `max_workers=5` is conservative for laptop-class hosts. Many production servers have 64-192+ cores and can saturate eval workers comfortably.

**Fix**: The skill's `eval.sh.template` defaults to `max_workers=16`; pass an arg to bump:
```bash
./eval.sh think 32     # use 32 parallel eval workers
```

Validated values:
| Host | max_workers | 500-inst eval time |
|---|---|---|
| 16-core | 5-8 | ~120 min |
| 64-core | 16 | ~40 min |
| 192-core (ion-h200-8) | 32 | ~20 min |

Going higher than 32 has diminishing returns (Docker daemon becomes the bottleneck).

## Recovery Procedures

### Full Reset (start fresh)
```bash
cd /root/swe-bench
rm -rf results/<mode>/
```

### Rerun Failed Instances Only
```bash
python3 /path/to/skill/scripts/clean_results.py results/<mode>/
./run.sh [--no-think] --workers 64
```

### Check Instance Status
```bash
# How many completed
ls results/<mode>/ | grep -c "^[a-z]"

# How many with patches
python3 -c "
import json
d = json.load(open('results/<mode>/preds.json'))
good = sum(1 for v in d.values() if v.get('model_patch','').strip())
print(f'{good}/{len(d)} have patches')
"
```
