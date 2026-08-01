# Terminal-Bench Harbor troubleshooting

## `all predefined address pools have been fully subnetted`

Cause: Harbor creates a Docker Compose network for each active task, and the daemon's default pools can be exhausted around 30 networks.

Evidence:

```bash
docker network ls
docker info
cat /etc/docker/daemon.json
```

On a dedicated runner, configure a larger default pool before the run:

```json
{"default-address-pools":[{"base":"172.30.0.0/16","size":24}]}
```

Restarting Docker affects every container. Do not do it on a shared runner without explicit authority. Jobs that already recorded network-creation failures are contaminated; start a new job after correction.

## Agent setup download reset

Claude Code and Pi may install their CLI during agent setup. `curl: (56) Recv failure: Connection reset by peer` before model execution is an infrastructure failure. Use `agent_setup_timeout_multiplier=3`, retain the failed smoke, and rerun under a new job name. Do not count the first failure as model evidence.

## `AgentTimeoutError`

This is Harbor's outer deadline for the agent phase, derived from the task timeout and multiplier. It does not mean SGLang timed out.

Harbor 0.20.0 can stop awaiting an agent without reliably killing every container-side child. A surviving agent or training process can race the verifier and invalidate the trial. Before interpreting the reward, inspect the task process tree, agent timestamps, verifier timestamps, and container lifecycle.

Changing or removing the deadline changes benchmark semantics. Preserve the bounded job and use a new job for an explicitly unbounded experiment.

## Effectively unbounded agent phase never finishes

`--agent-timeout-multiplier 1000000000` prevents Harbor from resolving agent-created subprocess waits. Examples include a foreground service piped to `tail`, an engine waiting on a protocol read, or a long search loop.

Distinguish a live computation from a wait:

```bash
docker stats --no-stream
docker top CONTAINER -eo pid,ppid,stat,etime,pcpu,pmem,args
find JOB/TRIAL/agent -type f -printf '%TY-%Tm-%TdT%TH:%TM:%TS %s %p\n'
```

Do not kill, add a timeout, edit files, or inject guidance when the requested policy is read-only. Report that the job is incomplete and let the user decide whether a recovery run is authorized.

## `inf` becomes `null`

Harbor 0.20.0 accepts an infinite timeout, but its JSON serialization writes `null` into `config.json` and `lock.json`. That loses the intended replay contract. Use a large finite multiplier and hash the resolved config.

## Claude Code HTTP 400 on a PDF

Claude Code's `Read` tool can replay a PDF as an Anthropic content block with `type: document`. A server schema that accepts text, image, tool, search, and thinking blocks but not `document` rejects the next `/v1/messages` request.

Look for:

```text
Input tag 'document' ... does not match any of the expected tags
```

This is an Anthropic API compatibility failure. It is not model reasoning failure, verifier failure, or agent timeout. Keep the stock result unchanged and test server support separately.

## Pi exits before a model request on a leading dash

Harbor 0.20.0 appends the escaped instruction directly after Pi flags without `--`. A task instruction beginning with `- ` becomes an unknown option. Confirm the instruction and agent log; classify it as a stock-adapter harness failure.

Task filters also require full package names:

```text
terminal-bench/nginx-request-logging
```

## Pi exits 137 after `pkill -f`

The full task instruction is present in the long-lived Pi argv. If the agent runs `pkill -9 -f NAME` and `NAME` appears in the instruction, Pi matches and kills itself. Confirm process argv and absence of an EC2/kernel OOM event before classifying it.

## High GPU utilization

GPU utilization near 100% means the server is doing work, not necessarily that it is overloaded. Inspect recent server logs for:

- running requests;
- queued requests;
- full-context and SWA token usage;
- throughput and speculative acceptance;
- HTTP status and fatal errors.

High utilization with zero queue and successful responses is healthy. Growing queue depth, request timeouts, or stalled completion count indicates saturation.

## Runner TTL expires

An EC2 CPU devbox uses ephemeral storage. Expiry can destroy incomplete job artifacts even when the GPU server remains healthy. Before pass@1, provision enough time for the slowest tail. Before pass@16, budget for 1,424 trials and extend both runner and server TTL. Verify the new expiry after every extension.

## `result.json` exists but the run is not done

Harbor writes aggregate results incrementally. Completion requires a non-null `finished_at`, all trials completed, no running/pending/cancelled trials, and the driver exit. A partial run may already have reward lists and a mean; those are descriptive only.

## Trial-local `result.json` is invalid JSON

Harbor redaction can insert a literal `[REDACTED]` rather than a quoted JSON string. Use the aggregate `result.json` for counts and rewards. Use `exception.txt`, session JSONL, agent logs, verifier `reward.txt`, `ctrf.json`, and test stdout for the failing trial.

## Score is far below an official reference

Audit in this order:

1. Dataset identity and task count.
2. Completion, cancellations, errors, and retries.
3. Harness and agent version.
4. API protocol and parser compatibility.
5. Reasoning mode, temperature, top-p, and token limits.
6. Agent timeout and leaked-process behavior.
7. Server revision and launch recipe.
8. Per-task flips against a matched run.

Do not attribute the entire gap to the model when the reference harness is unavailable or uses different sampling and tool policies.

## Pass@16 looks unexpectedly high or low

Verify that the job contains exactly 89 × 16 completed trials and no cancellations. Per-trial mean is not pass@16. Compute the union by canonical task name and require at least one reward-1 attempt. Use `scripts/summarize_job.py` and reconcile it with Harbor's own `pass_at_k` field.
