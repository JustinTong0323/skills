---
name: swe-bench
description: "Run SWE-bench Verified (the 500-instance coding benchmark) against any OpenAI-compatible endpoint. Use this skill whenever the user mentions SWE-bench, wants to benchmark a model on coding tasks, evaluate an LLM's code repair ability, run swebench verified, test a model served by SGLang or vLLM on software engineering tasks, or compare thinking vs non-thinking mode on SWE-bench. Also trigger when the user says 'run the benchmark', 'evaluate on swe-bench', 'test this endpoint on coding', or provides an API endpoint URL and asks to benchmark it."
version: 1.0.0
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
4. **Workers** — parallel instances, default 64

Optional parameters: `--eval-only`, `--instances ID1,ID2,...`, `--skip-pull`, `--step-limit N` (default 250), `--timeout N` (default 180s), `--container-timeout T` (default 12h).

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

**sglang_swebench.yaml** — Read `scripts/sglang_swebench.yaml.template` from this skill directory. Copy it and replace the `model_name` and `api_base` placeholders. Key settings that must be present:
- `model.cost_tracking: "ignore_errors"` — without this, zero-cost local models crash with "Cost must be > 0.0"
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
./run.sh [--no-think] --workers 64 [--filter '^(id1|id2)$']
```

For `--instances`, convert comma-separated IDs to a `--filter` regex.

Monitor with: `docker ps -q | wc -l` (running containers) and `ls results/<mode>/ | wc -l` (completed trajectories).

### Phase 6: Evaluate

Evaluation also needs Docker — it spins up containers to apply patches and run test suites.

```bash
./eval.sh <think|non-think>
```

The report JSON may land in `/root/swe-bench/` root (not in `results/`). Check both:
```bash
ls /root/swe-bench/hosted_vllm__*.json
ls results/<mode>/*.json
```

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

## Troubleshooting

Read `references/troubleshooting.md` for the full list of known issues and fixes. The most critical ones:

- **"Cost must be > 0.0"** — Add `cost_tracking: "ignore_errors"` to config
- **"Skipping N existing instances"** — Delete `exit_statuses_*.yaml` and clean empty entries from `preds.json`
- **Docker exit code 125** — Reduce workers, pre-pull images
- **Agent hangs at high step counts** — Container was killed; increase `container_timeout`
