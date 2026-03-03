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
