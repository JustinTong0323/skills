# Terminal-Bench Harbor troubleshooting

## `all predefined address pools have been fully subnetted`

Cause: Harbor creates a Docker Compose network for each active task, and the daemon's default pools can be exhausted around 30 networks.

Evidence:

```bash
docker network ls
docker info
cat /etc/docker/daemon.json
```

On a dedicated runner, configure a larger default pool before the run. The example range is illustrative; choose a range that does not overlap any runner, model endpoint, VPN, tunnel, or service route.

```json
{"default-address-pools":[{"base":"172.30.0.0/16","size":24}]}
```

Restarting Docker affects every container. Do not do it on a shared runner without explicit authority. Jobs that already recorded network-creation failures are contaminated; start a new job after correction.

## Image pull or environment build failures at launch

Launching many cold trials at once can overload registry access, Docker builds, disk I/O, or inode capacity. A reward-zero slot with a pull or build exception is infrastructure evidence, not model evidence.

Check `docker system df`, filesystem space and inodes, registry reachability, and the trial environment logs. Warm or validate images in bounded batches, then use a new job name. Do not reinterpret the contaminated aggregate as a clean score.

## Endpoint works on the host but not in task containers

Host-side probes do not exercise Docker routing. Test the exact API base from a container attached to the same network class as Harbor tasks. If a tunnel or port forward is required, run it independently of the control connection, persist its PID and logs, and monitor both process liveness and container-side HTTP reachability.

Do not hardcode a Docker gateway address from another machine. Resolve a routable address on the current runner and record the route as part of benchmark identity.

## Agent setup download reset

Claude Code and Pi may install their CLI during agent setup. `curl: (56) Recv failure: Connection reset by peer` before model execution is an infrastructure failure. Use `agent_setup_timeout_multiplier=3`, retain the failed smoke, and rerun under a new job name. Do not count the first failure as model evidence.

## Terminus-2 reports missing credentials before the first model request

Terminus-2's LiteLLM calls run in the Harbor launcher process. Values under the agent config's `env` are scoped to container execution and do not authenticate that host-side client. Source an owner-only launcher environment containing `OPENAI_API_KEY`, verify the key identity without printing its value, and start a new smoke. Treat the failed smoke as configuration evidence, not model evidence.

## QEMU tasks fail before natural grading

On non-metal EC2 runners, `qemu-alpine-ssh` and `qemu-startup` repeatedly produced the same runtime failure across independent campaigns. The official task images start QEMU without `-enable-kvm`, so the tasks run under TCG emulation regardless and requiring KVM would change the benchmark; a metal runner helps through CPU speed, not virtualization. Record the runner class, treat both tasks as runner-environment risks, and do not fold their zeros into a model-capability score.

## `AgentTimeoutError`

This is Harbor's outer deadline for the agent phase: each task's own base timeout (900, 1800, or 3600 s in TB2.1) multiplied by `agent_timeout_multiplier`. It is a per-task limit, not one fixed cap, and it does not mean SGLang timed out.

The renderer's capability policy writes `agent_timeout_multiplier=1000000000`, so a new capability-first job should not reach this exception in practice. Confirm the resolved config before rerunning. Task-defined deadline jobs may produce it legitimately, but the result measures time-to-solution under that deadline rather than capability alone.

Harbor 0.20.0 can stop awaiting an agent without reliably killing every container-side child. A surviving agent or training process can race the verifier and invalidate the trial. Before interpreting the reward, inspect the task process tree, agent timestamps, verifier timestamps, and container lifecycle.

Changing or removing the deadline changes benchmark semantics. Preserve the bounded job and use a new job for an explicitly unbounded experiment.

### Attribute a timeout by measurement, not by guess

The deadline covers the whole agent phase: every turn, every tool call, and all model time. Measure what the wall clock actually contained rather than reasoning from the agent's reputation. From the trial's session JSONL, split the span by timestamp gaps into model time, tool-execution time, and agent overhead; count model calls and their mean latency; tally output tokens per turn; and record the stop reason on every turn.

Read the result as:

- stop reason never `max_tokens` means the output cap is not the constraint. The model is stopping naturally to call tools and simply reasoning at length per turn.
- model time dominant means generation, not tooling. Combined with the per-stream throughput measurement from the contention preflight, this separates "the endpoint is slow" from "this harness generates far more tokens per task".
- input tokens far above the other arm mean the harness replays a larger context per turn and pays more prefill.
- a zero-byte patch with a healthy stream means the deadline landed between the agent's last edit and its `git commit`, or the container had no git identity; the work was done and never collected. Check the patch-collection boundary before blaming flags or transport.

One measured campaign: across 8 timed-out trials, 9,222 s of wall span split into 7,619 s model time (82.6 percent), 1,578 s tool time, and 26 s overhead, over 1,164 model calls averaging 6.5 s. Stop reason was a tool call on every turn and never the output cap. Timed-out trials emitted roughly 132k output tokens each against about 15.7k for an average trial, with single turns peaking above 38k tokens. The first explanation offered, that the harness ran many slow tools, was wrong and the measurement refuted it.

The decisive control is cross-arm: same endpoint, same concurrency, overlapping window, different harness. One arm producing 4 timeouts while another produced 15 rules out endpoint speed, because a slow endpoint penalizes both arms equally. In that campaign the slower arm also sent 3.4 times the input tokens for the same 89 tasks.

Raising `agent_timeout_multiplier` after seeing a bounded result does not repair that result. It creates a separate capability-policy evaluation that is not comparable to a published task-defined number. Preserve and label both.

## The same tasks stop timing out when rerun alone

A rerun of only the errored tasks fills fewer concurrent slots, so it runs at materially lighter load. When timeouts vanish under that lighter queue, contention in the parent run was the dominant cause and the parent score is a floor rather than a measurement.

One campaign measured this directly: a c32 run produced 4 timeouts in one arm and 18 in the other; rerunning only those tasks eliminated 4 of 4 and 13 of 18. Per-stream throughput was 382 tokens per second idle against about 139 at c32.

Do not reinterpret a subset rerun as the original task-defined score. Either accept the bounded score as deadline-limited, rerun every task at lower concurrency for a comparable bounded number, or run the capability policy as a separately labelled evaluation. Report the lighter effective load of any subset rerun as a known confound rather than treating its outcomes as equivalent draws.

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

Harbor 0.20.0 accepts an infinite timeout, but its JSON serialization writes `null` into `config.json` and `lock.json`. That loses the intended replay contract. Use a large finite multiplier and hash the resolved config. `summarize_job.py` refuses a `null` multiplier because the timeout policy cannot be recovered from it.

## `VerifierTimeoutError`, `AgentSetupTimeoutError`, `EnvironmentStartTimeoutError`

The agent phase ended normally and Harbor's verifier, agent-setup, or environment-start deadline fired instead. These are infrastructure outcomes: `summarize_job.py` lists them under `infrastructure_exception_tasks`, marks the score invalid in either mode, and Harbor's default retry policy excludes `VerifierTimeoutError`. Rerun the affected tasks in a new job; raise `--verifier-timeout-multiplier` or `--agent-setup-timeout-multiplier` only as an explicitly recorded config change.

## Several trials disconnect in the same millisecond

Identical `ApiConnectionClosedError` or `ApiResponseStalledError` timestamps across trials while the server stays healthy point at a shared transport such as one port forward, not at the tasks. The renderer's default retry list includes both types; a job retrying only `RuntimeError` and `NetworkConnectionError` loses every such trial with no retry. Classify by trajectory start time against the transport event, do not restart a live forward mid-run, and drain old connections before routing new ones elsewhere.

## Reward 0 with no exception but the verifier never judged

A package-index or registry failure during verifier setup, such as a PyPI 502, can leave reward 0 with no exception. Check the verifier setup log for every reward-0 trial before counting it as a model loss; such trials are ungraded and belong in a rerun.

## Harbor omits `n_attempts` or concurrency from `config.json`

Harbor 0.20.0 persists configs with default-valued fields excluded. Missing `n_attempts` means the documented default 1, and missing `n_concurrent_trials` means the documented default 4. The bundled summary and comparison tools resolve those two defaults only when `config.json` exists. Do not infer them when the file itself is missing.

## Claude Code HTTP 400 on a PDF

Claude Code's `Read` tool can replay a PDF as an Anthropic content block with `type: document`. A server schema that accepts text, image, tool, search, and thinking blocks but not `document` rejects the next `/v1/messages` request.

Look for:

```text
Input tag 'document' ... does not match any of the expected tags
```

This is an Anthropic API compatibility failure. It is not model reasoning failure, verifier failure, or agent timeout. The rejected block stays in the conversation, so every later request in the session fails as well; count the whole trial as a protocol failure. Keep the stock result unchanged, and for a text-only endpoint configure the scoped `--claude-disallowed-tools` deny described in [harnesses.md](harnesses.md) as a separately labelled run.

## Claude Code effort passes smoke but fails later

The configured effort label is not necessarily the only value emitted by the agent. A run configured for `max` can still exercise a related value such as `high`, and the server or model template may accept one while rejecting the other.

Inspect agent requests and server validation errors, probe every observed effort value after each server restart, and repeat the set at intended concurrency. A one-task smoke that covered only one value is insufficient. Fixes to effort normalization change the runtime identity and require a fresh smoke and job.

## Pi exits before a model request on a leading dash

Harbor 0.20.0 appends the escaped instruction directly after Pi flags without `--`. A task instruction beginning with `- ` becomes an unknown option. Confirm the instruction and agent log; classify it as a stock-adapter harness failure.

This failure is deterministic. Harbor rebuilds an identical command every run, so the task fails the same way on every attempt and the model never attempts it at all. One task reproduced it three times out of three, then passed on a patched adapter. See the file-argument remedy in [harnesses.md](harnesses.md).

Task filters also require full package names:

```text
terminal-bench/nginx-request-logging
```

## Pi exits 143 or 137 after a pattern kill

The full task instruction is present in the long-lived Pi argv. If the agent runs a pattern kill whose pattern appears in that instruction, Pi matches itself and dies:

- a plain `pkill -f NAME` sends SIGTERM, so the trial reports **exit 143**;
- `pkill -9 -f NAME` sends SIGKILL, so it reports exit 137.

Exit 143 is the case observed in practice, three times in one campaign. Search agent logs for both codes, and for executed `pkill`, `pgrep`, and `killall` commands. Confirm the process argv and rule out a host or kernel OOM before classifying.

The exposed set is every task whose instruction names a file, not the tasks whose instruction mentions a kill command. The agent invents the kill; the instruction only has to supply the filename, which ordinary cleanup then matches. In one campaign the kill interrupted a `pkill -f NAME; sleep 2; pgrep ...; rm -rf ...` cleanup sequence, which is why such trials can leave odd container state.

The failure is stochastic and can change error class between attempts. One task went timeout, then self-kill, then timeout across three attempts, while another self-killed once and later passed. The harness tax is therefore a per-run lottery rather than a fixed set of tasks to subtract. Report a harness-adjusted score only for deterministic failures, and attach the attempt number to any adjustment.

## Estimating harness tax before a run

A scan of instruction text can find deterministic harness failures, such as instructions beginning with `- `. It cannot bound agent-triggered ones, because the agent's own commands are not in the instruction.

Report such a scan as a lower bound on deterministic tax only, never as "the harness tax is N tasks". On one TB2.1 run the scan found no kill-command mentions and predicted a tax of exactly one task; the run then produced that one deterministic failure plus a stream of self-kills the scan could not see.

## High GPU utilization

GPU utilization near 100% means the server is doing work, not necessarily that it is overloaded. Inspect recent server logs for:

- running requests;
- queued requests;
- full-context and SWA token usage;
- throughput and speculative acceptance;
- HTTP status and fatal errors.

High utilization with zero queue and successful responses is healthy. Growing queue depth, request timeouts, or stalled completion count indicates saturation.

## Runner or server lease expires

An ephemeral CPU runner can lose incomplete job artifacts even when the GPU server remains healthy. Before pass@1, provision enough time for the slowest tail. Before pass@16, budget for 1,424 trials and extend both CPU and GPU resource leases. Verify the new expiry after every extension.

## `result.json` exists but the run is not done

Harbor writes aggregate results incrementally. Completion requires a non-null `finished_at`, all trials completed, no running/pending/cancelled trials, and the driver exit. A partial run may already have reward lists and a mean; those are descriptive only.

## A stopped run says every trial completed

Harbor can finalize cancelled slots into `n_completed_trials` and include them in the aggregate mean's denominator. The file may therefore say 89/89 completed while reward lists contain fewer than 89 graded outcomes and `n_cancelled_trials` is nonzero.

Use `scripts/summarize_job.py` to report graded reward records, cancellations, and the partial lower-to-upper range. Do not publish the post-cancellation mean as a benchmark score. Preserve the last watchdog snapshot because another task may complete between the cutoff decision and Harbor shutdown.

## Stop when a target score becomes impossible

Define the target and stopping policy before launch. Poll with:

```bash
python3 "$TB_HARBOR_SKILL_DIR/scripts/summarize_job.py" JOB \
  --target-passes TARGET \
  --fail-if-target-unreachable
```

Exit 3 means the optimistic ceiling is below the target. Equality can still tie and is reachable. Other nonzero exits indicate malformed or unavailable state and must not stop the run.

Run the watchdog detached from the control-plane transport. Log the timestamped pass, failure, graded, running, pending, retry, and error counts before signalling the validated Harbor driver PID. Keep its detached wrapper alive to record the exit status, and retain both the pre-stop snapshot and finalized cancelled artifact.

## A rerun changed more than its job name

Text diffs and config hashes alone do not prove equivalence. Render both resolved configs and run:

```bash
python3 "$TB_HARBOR_SKILL_DIR/scripts/compare_configs.py" PRIOR/config.json RERUN/config.json
```

The command ignores top-level `job_name`, canonicalizes the set-valued retry exception lists, and reports every remaining differing JSON path. If either side contains Harbor's persisted `${VAR}`, `****`, `first4****last3`, or `[REDACTED]` form for a sensitive environment value, the command reports that path as unknown; two literal values remain strictly comparable. Verify unknown key identity separately. Any additional difference requires an explicit comparison rationale or a new experiment axis.

For Pi, compare the recorded `TB_PI_MODELS_SEMANTIC_SHA256` value and mount source, archive the content-addressed registry, and verify its exact file hash. If the renderer refuses an existing registry because its content differs, do not overwrite it; use a new base path after resolving the unexpected credential or file drift.

## Runtime image or checkout drift

A local branch SHA, remote checkout SHA, container image tag, and running server revision are separate claims. Moving image tags and stale processes can serve code that differs from the intended checkout.

Record the immutable image digest, read the revision from inside the runtime, capture the exact launch command, and probe the live API. For task-side failures, also record the Terminal-Bench environment image digest. A protocol error correlated with one content type is not enough evidence of a task-image failure; check HTTP status, server validation markers, task-container setup, and whether unaffected tasks use the same image.

## Releasing benchmark resources

Ephemeral CPU runners and GPU-server scratch may be wiped on release. Archive aggregates, resolved configs, cutoff snapshots, and relevant logs first, then verify local or durable-storage hashes.

Resolve exact resource IDs and inspect current processes before release. Stop or release only dedicated resources for the completed benchmark; a shared server may belong to another project. Do not use a data-wipe option unless deletion of persistent storage was separately authorized. After release, confirm the exact resources are gone.

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

If either job lacks `config.json`, report configuration equivalence as unknown. Two missing configuration artifacts are not evidence that the runs were equivalent. `summarize_job.py` requires a positive `n_attempts` value from the resolved config instead of inferring pass@k from an incomplete artifact set.

## Pass@16 looks unexpectedly high or low

Verify that the job contains exactly 89 × 16 completed trials and no cancellations. Per-trial mean is not pass@16. Compute the union by canonical task name and require at least one reward-1 attempt. Use `scripts/summarize_job.py` and reconcile it with Harbor's own `pass_at_k` field.
