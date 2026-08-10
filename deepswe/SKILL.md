---
name: deepswe
description: Run, audit, score, resume, and troubleshoot the 113-task datacurve-ai/deep-swe coding-agent benchmark with Pier or Harbor. Use when a user mentions DeepSWE, wants to evaluate a coding agent or model endpoint on DeepSWE, reproduce the public Pier leaderboard workflow, use Harbor as an alternative runner, or use Kimi Code as an optional DeepSWE agent.
version: 1.0.0
---

# DeepSWE Runner

Run DeepSWE without conflating the task corpus, orchestration backend, agent harness, model endpoint, and scoring policy.

## Choose the backend and agent independently

| Goal | Backend | Agent |
|---|---|---|
| Reproduce the DeepSWE public workflow | Pier | `mini-swe-agent` |
| Run the current task format locally | Harbor 0.20.0 or Pier 0.3.0 | Any compatible agent |
| Run the Kimi Vendor Verifier profile | Pier | Kimi Code 0.23.6 or newer |
| Evaluate current npm Kimi Code outside the KVV profile | Pier | Kimi Code |

Pier remains the canonical comparison path because DeepSWE says its published leaderboard scores were produced with Pier, `mini-swe-agent`, and Modal. Harbor 0.20.0 is a supported alternative: all 113 task configs load with separate verifier environments, collect hooks, and agent network isolation. Do not label a Harbor result as a strict reproduction of a Pier leaderboard result.

Read [references/backends.md](references/backends.md) before selecting or upgrading a backend. Read [references/kimi-code.md](references/kimi-code.md) only when Kimi Code is selected.

## Gather inputs

Obtain or discover:

- backend and execution environment: Pier or Harbor; Docker or Modal;
- agent and exact agent version;
- model ID, endpoint URL, API protocol, and API key environment variable;
- context window, output limit, thinking mode, and request fields;
- full 113-task run, deterministic subset, or named task;
- concurrency, timeout policy, retry policy, output directory, and job name;
- comparison target and whether it requires the official Pier workflow or KVV profile.

Never put an API key in a committed config, captured HTTP transcript, or shared log. Pass secrets through an environment variable and protect result directories because resolved job configs or process diagnostics may contain agent environment values.

## Preserve the benchmark identity

Pin and record:

- DeepSWE commit;
- backend package version and source commit when installed from Git;
- agent package version and adapter hash when applicable;
- model/server revision and launch recipe;
- task count and selected task names;
- environment type, concurrency, timeouts, retries, and sampling/thinking settings.

Read [references/upstream.md](references/upstream.md) before changing revisions or making comparison claims.

Set up a pinned corpus:

```bash
git clone https://github.com/datacurve-ai/deep-swe.git
git -C deep-swe checkout DEEPSWE_COMMIT
python3 <skill-dir>/scripts/audit_tasks.py \
  --tasks deep-swe/tasks \
  --expected 113
```

The audit must report 113 valid task contracts. Do not expose `solution/` or held-out verifier material to the agent.

## Protect and preflight the runner

Use a dedicated Docker-capable CPU runner for a full run. Inspect existing processes, containers, disk, memory, CPU, and Docker networks before launch:

```bash
docker version
docker info
docker ps
docker system df
df -h / /var/lib/docker
df -ih / /var/lib/docker
free -h
nproc
```

DeepSWE images, writable layers, and retained logs can consume substantial disk. Never prune resources belonging to another run. Verify Docker registry authentication before a cold full run.

If the endpoint is external, test it from a disposable container, not only from the host. If a host-local tunnel is required, bind its relay only to the Docker bridge gateway rather than `0.0.0.0`:

```bash
BRIDGE_GATEWAY="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')"
socat TCP-LISTEN:30011,fork,reuseaddr,bind="$BRIDGE_GATEWAY" TCP:127.0.0.1:30001
docker run --rm curlimages/curl:latest \
  -fsS "http://$BRIDGE_GATEWAY:30011/v1/models"
```

Do not use `socat -v` with authenticated model traffic. It records request headers and bodies, including credentials and prompts.

## Install one backend

For the canonical Pier path:

```bash
uv tool install 'datacurve-pier==0.3.0'
pier --version
```

For Harbor:

```bash
uv tool install 'harbor==0.20.0'
harbor --version
```

Treat these as the validated versions, not floating requirements. Re-run the task audit and both smoke gates before accepting a newer version.

## Run smoke gates

Use a named task such as `abs-module-cache-flags` for both gates.

An oracle run can validate image startup and verifier execution without calling a model:

```bash
pier run -p deep-swe/tasks/abs-module-cache-flags \
  --agent oracle \
  --job-name deepswe-oracle-smoke \
  --jobs-dir "$DEEPSWE_JOBS" \
  --yes
```

Harbor equivalent:

```bash
harbor run --path deep-swe/tasks/abs-module-cache-flags \
  --agent oracle \
  --job-name deepswe-oracle-smoke \
  --jobs-dir "$DEEPSWE_JOBS" \
  --yes
```

Do not use the built-in oracle as a reward or patch-collection gate for
DeepSWE v1.1. Pier 0.3.0's oracle does not commit the reference changes before
the task's `git diff BASE HEAD` collect hook, so it can finish with an empty
artifact set and reward 0. Preserve and report that result rather than treating
it as a solved task. The real-agent smoke below is mandatory.

Then run one real agent task. For the canonical `mini-swe-agent` path with Pier:

```bash
pier run -p deep-swe/tasks/abs-module-cache-flags \
  --agent mini-swe-agent \
  --model hosted_vllm/MODEL_ID \
  --agent-kwarg model_class=litellm \
  --agent-env OPENAI_BASE_URL="$MODEL_BASE_URL" \
  --agent-env MSWEA_API_KEY="$MODEL_API_KEY" \
  --job-name deepswe-agent-smoke \
  --jobs-dir "$DEEPSWE_JOBS" \
  --yes
```

Provider prefixes and agent kwargs depend on the endpoint and selected agent. Verify the exact adapter contract before launch. For Kimi Code commands, use [references/kimi-code.md](references/kimi-code.md).

The real-agent smoke must prove:

- endpoint reachability from the task container;
- pinned agent installation and version detection;
- at least one model/tool loop;
- patch collection into the separate verifier environment;
- a valid binary verifier reward, even when the model does not solve the task;
- no leaked secret in retained logs.

## Run the selected scope

Pier full-run shape:

```bash
pier run -p deep-swe/tasks \
  --agent mini-swe-agent \
  --model hosted_vllm/MODEL_ID \
  --agent-kwarg model_class=litellm \
  --agent-env OPENAI_BASE_URL="$MODEL_BASE_URL" \
  --agent-env MSWEA_API_KEY="$MODEL_API_KEY" \
  --n-concurrent 8 \
  --job-name deepswe-MODEL-AGENT-r1 \
  --jobs-dir "$DEEPSWE_JOBS" \
  --yes
```

Harbor full-run shape:

```bash
harbor run --path deep-swe/tasks \
  --agent mini-swe-agent \
  --model hosted_vllm/MODEL_ID \
  --allow-agent-host MODEL_ENDPOINT_HOST \
  --agent-env OPENAI_BASE_URL="$MODEL_BASE_URL" \
  --agent-env MSWEA_API_KEY="$MODEL_API_KEY" \
  --n-concurrent 8 \
  --job-name deepswe-MODEL-AGENT-r1 \
  --jobs-dir "$DEEPSWE_JOBS" \
  --yes
```

Start concurrency conservatively. Long agent histories can saturate endpoint context/KV capacity even when request count is below the server limit. Keep retry policy limited to declared infrastructure failures; retrying model, timeout, or verifier outcomes changes evaluation semantics.

Run long jobs detached with a durable log, driver PID, and exit-status marker. The existence of aggregate `result.json` is not a completion signal because runners update it incrementally.

## Resume an interrupted job

Resume from the preserved job directory so the runner can reconcile completed trials against its stored config:

```bash
pier job resume --job-path "$DEEPSWE_JOBS/JOB_NAME"
harbor job resume --job-path "$DEEPSWE_JOBS/JOB_NAME"
```

Both commands retry cancelled trials by default and retain completed trials. Passing another `--filter-error-type` removes matching failed trial directories before resume and creates a new attempt for those tasks. Do that only for an explicitly approved infrastructure recovery, preserve the prior artifacts, and report the retry.

## Monitor without changing the run

At a fixed interval check:

- driver process and recorded exit status;
- completed, running, pending, errored, retried, and cancelled trials;
- task container count and log growth;
- endpoint and tunnel health from the container-visible route;
- disk, inodes, Docker usage, memory, and I/O pressure.

Do not kill a slow task based only on elapsed time. Classify runner, image, network, endpoint, agent, context, and verifier failures first. Read [references/troubleshooting.md](references/troubleshooting.md) for failure routing.

## Score against the full expected set

An errored or missing task contributes zero. Never average only the rewards that exist in `reward_stats`.

```bash
python3 <skill-dir>/scripts/summarize_job.py \
  "$DEEPSWE_JOBS/deepswe-MODEL-AGENT-r1" \
  --expected 113 \
  --require-complete
```

For a subset, pass its exact planned count. The strict score is:

```text
sum(binary reward for every planned task, using 0 for error/missing) / planned tasks
```

Report:

- resolved / planned and strict score;
- graded rewards, errored trials, and ungraded trials;
- completion state, retries, and cancellations;
- corpus commit, backend, environment, agent/version, model/server revision;
- endpoint protocol, context limit, thinking/sampling settings, timeouts, retries, and concurrency;
- any infrastructure incident and whether a retry changed the original trial set.

Do not report the observed mean over graded trials as the benchmark score. Do not compare Harbor and Pier results as a model-only A/B unless agent, prompt, network, timeout, retry, and verifier behavior are demonstrated equivalent.
