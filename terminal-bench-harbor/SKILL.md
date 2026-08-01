---
name: terminal-bench-harbor
description: "Run Terminal-Bench 2.1 with Harbor against a local or remote SGLang/OpenAI/Anthropic-compatible endpoint. Use whenever the user mentions Terminal-Bench, TB2.1, Harbor evaluation, Terminus-2, Claude Code or Pi as a Harbor harness, pass@1/pass@k, or asks to benchmark an agentic model in Docker terminal tasks. Covers runner sizing, endpoint preflight, reproducible configs, smoke gates, detached execution, read-only monitoring, completion audits, harness comparison, and failure attribution."
version: 1.0.0
---

# Terminal-Bench with Harbor

Run Terminal-Bench through Harbor without conflating model quality, agent-harness behavior, serving behavior, and runner infrastructure.

## Quick reference

| Component | Role |
|---|---|
| Harbor | Dataset resolution, trial orchestration, Docker environments, verification, aggregation |
| `terminal-bench/terminal-bench-2-1` | 89-task Terminal-Bench 2.1 package |
| Terminus-2 | OpenAI-compatible reference harness with explicit sampling controls |
| Claude Code | Anthropic Messages harness with its own tool loop and sampling policy |
| Pi | OpenAI Completions harness configured through a mounted `models.json` |
| SGLang | Model server; keep its revision and launch recipe fixed across harness comparisons |
| EC2 CPU devbox | Harbor/Docker runner; keep it separate from the GPU server |

## Required information

Gather or discover these before writing a config:

1. Harbor runner location and whether it is dedicated or shared.
2. Host-visible API base URL. A runner must reach it directly; `localhost` inside a task container is not the runner or GPU server.
3. Served model ID, context window, and maximum output tokens.
4. Agent harness: `terminus-2`, `claude-code`, or `pi`.
5. Reasoning mode and sampling settings supported by that harness.
6. Trial concurrency, attempts per task, and runner TTL.
7. Agent-timeout and retry policy. These change evaluation semantics and must never be silently changed.
8. Dataset ref. Pin the resolved digest instead of relying on `latest` for a scored run.

For the 89-task TB2.1 package used in the validated Harbor 0.20.0 run:

```text
terminal-bench/terminal-bench-2-1@sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a
```

Re-resolve and record the digest when intentionally testing a newer dataset revision.

## Evaluation invariants

- Pin Harbor version, dataset digest, agent version, model ID, server revision and launch command, config hash, concurrency, attempts, timeout policy, retry policy, and runner type.
- Use a new job name for every smoke, rerun, harness, or policy change. Never overwrite or reinterpret a contaminated job.
- Change one comparison axis at a time. A harness comparison is not a sampling-only comparison when the harness does not expose the same controls.
- Treat infrastructure, harness, API, agent, verifier, and model failures as different categories even when all receive reward zero.
- Do not intervene in an active agent unless the user authorized recovery. Killing a process, injecting guidance, editing task files, or adding a timeout changes the run.
- Do not publish a partial aggregate as a final score.

## Phase 0: inspect journals and preserve the server recipe

Search the applicable Journal store for the model, dataset digest, Harbor version, agent, exception type, prior job name, and server launch flags. Revalidate load-bearing claims against the live environment.

For SGLang, verify:

```bash
curl -fsS "$OPENAI_BASE/models"
curl -fsS -o /dev/null -w '%{http_code}\n' "$SERVER_ROOT/health"
```

Probe the actual reasoning and tool-call contract with a small request before Harbor. Keep the model-specific reasoning parser, tool parser, context length, quantization, parallelism, and speculative algorithm unchanged during a harness A/B.

DeepSeek-V4-Flash-0731 specifically uses its bundled DSpark head with:

```text
--speculative-algorithm DSPARK
```

Do not substitute `DRAFT_EXTEND`, `EAGLE`, or a separate draft model for that recipe.

## Phase 1: provision and preflight the Harbor runner

Use a dedicated Docker-capable CPU runner when possible. Observed sizing for local Harbor Docker trials:

| Concurrency | Practical runner |
|---|---|
| 8-16 | `c7i.4xlarge`, 16 vCPU / 32 GiB |
| 16-24 | `m7i.8xlarge`, 32 vCPU / 128 GiB |
| 32 | `r7i.12xlarge`, 48 vCPU / 384 GiB |

At c32, memory was not the limiting resource: the observed first wave used about 4.5 GiB while load reached 37 on 48 vCPU. Avoid a 128-vCPU/1-TiB runner unless another workload needs it. Use a 1-TB disk for images, task layers, and retained artifacts.

Check the runner:

```bash
harbor --version
docker version
docker info
df -h / /var/lib/docker
docker ps
```

Install a pinned Harbor version with `uv` when absent:

```bash
uv tool install 'harbor==0.20.0'
```

Harbor creates one Docker Compose network per active task. An unmodified daemon may exhaust its predefined pools near 30 networks even when CPU, memory, disk, and SGLang are healthy. On a dedicated runner, configure enough `/24` networks before starting trials:

```json
{"default-address-pools":[{"base":"172.30.0.0/16","size":24}]}
```

Changing `/etc/docker/daemon.json` and restarting Docker disrupts existing containers. Inspect first and obtain authority when the runner is shared. A c32 run is not validated until the host can create and delete at least 40 probe networks and then hold 32 real task networks.

## Phase 2: render a reproducible Harbor config

Set `TB_HARBOR_SKILL_DIR` to the directory containing this `SKILL.md`, then use the bundled renderer:

```bash
TB_HARBOR_SKILL_DIR=/absolute/path/to/terminal-bench-harbor
python3 "$TB_HARBOR_SKILL_DIR/scripts/render_config.py" \
  --agent terminus-2 \
  --job-name tb21-MODEL-terminus-c32-k1 \
  --jobs-dir /home/ubuntu/tb21/jobs \
  --model MODEL_ID \
  --api-base http://SERVER:30000/v1 \
  --dataset-ref sha256:DATASET_DIGEST \
  --context-window MODEL_CONTEXT \
  --max-output-tokens MODEL_MAX_OUTPUT \
  --reasoning-effort REASONING_MODE \
  --concurrency 32 \
  --attempts 1 \
  --output /home/ubuntu/tb21/configs/terminus-k1.json
```

The renderer supports all three harnesses and writes owner-only JSON. See [harnesses.md](references/harnesses.md) for exact differences and additional flags.

Timeout policy must be explicit:

- Omit `--agent-timeout-multiplier` to retain task-defined benchmark deadlines.
- Use a finite, auditable multiplier such as `1000000000` only when the user explicitly requests an effectively unbounded agent phase.
- Do not use `inf` with Harbor 0.20.0. It is accepted at runtime but serialized as `null`, losing replay semantics.
- Setup and verifier timeouts remain separate. `--agent-setup-timeout-multiplier 3` is useful for CLI installation and network variance.

Restrict retries to infrastructure exceptions unless the evaluation protocol says otherwise:

```text
RuntimeError
NetworkConnectionError
```

Do not silently retry `AgentTimeoutError`, `NonZeroAgentExitCodeError`, verifier failures, or model answers.

## Phase 3: run two smoke gates

First verify Harbor, Docker, the dataset, and the verifier without a model:

```bash
harbor run \
  --job-name tb21-oracle-smoke \
  --jobs-dir /home/ubuntu/tb21/jobs \
  --dataset terminal-bench/terminal-bench-2-1@sha256:DATASET_DIGEST \
  --include-task-name terminal-bench/nginx-request-logging \
  --agent oracle \
  --n-concurrent 1 \
  --yes
```

Then run the chosen harness config on the same single task. Package filters require the full `terminal-bench/<task>` name; `nginx-request-logging` alone is rejected before trial creation.

```bash
harbor run --config /home/ubuntu/tb21/configs/HARNESS-smoke.json --yes
```

The smoke must prove all of these:

- endpoint reachability from the task container;
- agent installation and model discovery;
- reasoning and tool-call parsing;
- at least one real tool loop;
- trajectory and log capture;
- artifact collection and verifier execution;
- reward 1 with no agent, API, environment, or verifier exception.

A direct API probe alone does not authorize the 89-task run.

## Phase 4: start the full run detached

Long `rx devbox run` or SSH commands can die with the transport. Start Harbor in a detached session on the runner and write a PID, log, and exit-status file. Never use the existence of `result.json` as the done signal; Harbor writes it incrementally.

```bash
setsid bash -lc 'harbor run --config /home/ubuntu/tb21/configs/RUN.json --yes > /home/ubuntu/tb21/RUN.log 2>&1; printf "%s\n" "$?" > /home/ubuntu/tb21/RUN.done' </dev/null &
printf '%s\n' "$!" > /home/ubuntu/tb21/RUN.pid
```

Scale from the successful smoke. c32 was validated with a 48-vCPU runner, corrected Docker pools, and an SGLang server whose request queue stayed at zero. GPU utilization alone does not prove overload; inspect running requests, queued requests, KV/SWA pressure, response errors, and completion rate.

## Phase 5: monitor without changing the run

Use short, read-only polls:

```bash
python3 "$TB_HARBOR_SKILL_DIR/scripts/summarize_job.py" /home/ubuntu/tb21/jobs/RUN
ps -p "$(cat /home/ubuntu/tb21/RUN.pid)" -o pid=,stat=,etime=,cmd=
docker ps --format '{{.Names}}|{{.Status}}'
find /home/ubuntu/tb21/jobs/RUN -mindepth 2 -maxdepth 2 -name result.json | wc -l
curl -fsS -o /dev/null -w '%{http_code}\n' "$SERVER_ROOT/health"
```

When progress stops, classify before acting:

1. Is the Harbor driver alive?
2. Are task containers alive?
3. Are agent logs changing?
4. Is the task waiting on a child process, model request, or verifier?
5. Is SGLang healthy, serving, queued, or emitting errors?
6. Did the runner hit CPU, disk, memory, network-pool, or TTL limits?

Elapsed time alone is not failure evidence. Under an unbounded policy, a child process can wait forever; report it and preserve the run unless recovery is authorized.

## Phase 6: completion audit

Declare a job complete only when authoritative evidence proves all conditions:

- `finished_at` is non-null;
- `n_completed_trials == n_total_trials`;
- running, pending, and cancelled counts are zero;
- the Harbor driver exited and its exit status is known;
- task containers for the job are gone when deletion is enabled;
- aggregate `result.json` and `config.json` hashes are recorded;
- errors and retries are enumerated by type and trial;
- the SGLang server remained healthy or every outage interval is accounted for.

Run:

```bash
python3 "$TB_HARBOR_SKILL_DIR/scripts/summarize_job.py" /home/ubuntu/tb21/jobs/RUN --json
sha256sum /home/ubuntu/tb21/jobs/RUN/result.json /home/ubuntu/tb21/jobs/RUN/config.json
```

Trial-local JSON can be invalid after Harbor redaction inserts a literal `[REDACTED]`. Prefer the valid aggregate and inspect `exception.txt`, agent logs, session JSONL, and verifier artifacts for that trial.

## Phase 7: compare harnesses and report

Compare only complete matched jobs by default:

```bash
python3 "$TB_HARBOR_SKILL_DIR/scripts/compare_jobs.py" \
  /home/ubuntu/tb21/jobs/LEFT/result.json \
  /home/ubuntu/tb21/jobs/RIGHT/result.json
```

Report:

- passed tasks / 89 and mean reward;
- exact dataset digest and attempts;
- wall time, tokens, and reported cost separately;
- error and retry counts by category;
- paired both-pass, both-fail, left-only, and right-only tasks;
- harness, protocol, reasoning, sampling, timeout, and server differences;
- infrastructure incidents and whether they affected scoring.

An official-score gap is not a model regression until harness and protocol differences are controlled. For DeepSeek-V4-Flash-0731, the published 82.7% uses an unreleased DeepSeek Harness in minimal mode with maximum reasoning, temperature 1, and top-p 0.95. Claude Code and stock Pi are not strict reproductions of that setup.

## Phase 8: pass@k

Harbor uses `n_attempts`; TB2.1 pass@16 is 89 × 16 = 1,424 trials. Keep trial concurrency fixed unless intentionally testing capacity, and extend both runner and server TTL before launch.

```bash
python3 "$TB_HARBOR_SKILL_DIR/scripts/render_config.py" \
  ... \
  --attempts 16 \
  --job-name tb21-MODEL-HARNESS-c32-k16 \
  --output /home/ubuntu/tb21/configs/HARNESS-k16.json
```

Distinguish:

- per-trial mean reward: average pass@1 across all sampled attempts;
- pass@16 union: fraction of tasks with at least one success among 16 attempts.

Validate Harbor's pass-at-k output independently with `summarize_job.py`. It groups trial IDs by the task prefix before the final `__<trial-suffix>`. A partial or cancelled 1,424-trial job has no final pass@16, even if some tasks already succeeded.

## Troubleshooting routing

Read [troubleshooting.md](references/troubleshooting.md) for Docker network exhaustion, setup download resets, timeout process leaks, Anthropic document blocks, Pi argv bugs, self-termination, unbounded subprocess waits, TTL expiry, server-load interpretation, partial jobs, and suspicious official-score gaps.
