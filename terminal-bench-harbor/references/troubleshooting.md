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

## A stopped run says every trial completed

Harbor can finalize cancelled slots into `n_completed_trials` and include them in the aggregate mean's denominator. The file may therefore say 89/89 completed while reward lists contain fewer than 89 graded outcomes and `n_cancelled_trials` is nonzero.

Use `scripts/summarize_job.py` to report graded reward records, cancellations, and the partial lower-to-upper range. Do not publish the post-cancellation mean as a benchmark score. Preserve the last watchdog snapshot because another task may complete between the cutoff decision and Harbor shutdown.

## Stop when a target score becomes impossible

Define the target and stopping policy before launch. Poll with:

```bash
python3 scripts/summarize_job.py JOB \
  --target-passes TARGET \
  --fail-if-target-unreachable
```

Exit 3 means the optimistic ceiling is below the target. Equality can still tie and is reachable. Other nonzero exits indicate malformed or unavailable state and must not stop the run.

Run the watchdog detached from the control-plane transport. Log the timestamped pass, failure, graded, running, pending, retry, and error counts before signalling the validated Harbor driver PID. Keep its detached wrapper alive to record the exit status, and retain both the pre-stop snapshot and finalized cancelled artifact.

## A rerun changed more than its job name

Text diffs and config hashes alone do not prove equivalence. Render both resolved configs and run:

```bash
python3 scripts/compare_configs.py PRIOR/config.json RERUN/config.json
```

The command ignores top-level `job_name` and reports every remaining differing JSON path. Any additional difference requires an explicit comparison rationale or a new experiment axis.

## Runtime image or checkout drift

A local branch SHA, remote checkout SHA, container image tag, and running server revision are separate claims. Moving image tags and stale processes can serve code that differs from the intended checkout.

Record the immutable image digest, read the revision from inside the runtime, capture the exact launch command, and probe the live API. For task-side failures, also record the Terminal-Bench environment image digest. A protocol error correlated with one content type is not enough evidence of a task-image failure; check HTTP status, server validation markers, task-container setup, and whether unaffected tasks use the same image.

## Releasing benchmark resources

Ephemeral CPU runners and per-devbox scratch are wiped on release. Archive aggregates, resolved configs, cutoff snapshots, and relevant logs first, then verify local or durable-storage hashes.

Resolve exact devbox IDs and inspect current processes before release. Stop or release only dedicated resources for the completed benchmark; a shared server may belong to another project. Do not use a data-wipe option unless deletion of the persistent prefix was separately authorized. After release, confirm the exact entries disappear from the resource list.

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

Repeat a surprising pass@1 result with a new job identity and a structurally equivalent config. Compare only tasks naturally graded in both jobs, report left-only and right-only passes, and separate net score movement from total task churn. Large bidirectional churn indicates stochastic variance even when the headline scores are close.

## Pass@16 looks unexpectedly high or low

Verify that the job contains exactly 89 × 16 completed trials and no cancellations. Per-trial mean is not pass@16. Compute the union by canonical task name and require at least one reward-1 attempt. Use `scripts/summarize_job.py` and reconcile it with Harbor's own `pass_at_k` field.
