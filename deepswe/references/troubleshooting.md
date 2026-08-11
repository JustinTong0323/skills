# Troubleshooting

## Harbor emits an artifact-overlap warning

Current tasks explicitly collect `/logs/artifacts/model.patch` while Harbor also collects the conventional `/logs/artifacts` directory. Confirm the resulting artifact directory contains `model.patch` and the verifier produced a reward. Do not suppress unrelated artifact warnings.

## An error-heavy run reports an unexpectedly high score

The runner's metric may average only trials with verifier rewards. Run `scripts/summarize_job.py` with the full planned count. Errored, cancelled, pending, and missing rewards contribute zero to the strict score.

## Kimi Code is unknown on Pier

Set `PYTHONPATH` in the same shell or detached wrapper that launches Pier and use:

```text
--agent-import-path kimi_code_agent:KimiCode
```

Confirm the path contains this skill's `scripts/kimi_code_agent.py`.

## Kimi Code is unknown on Harbor

Harbor 0.20.0 lists the legacy Python `kimi-cli` agent, not the current npm
`@moonshot-ai/kimi-code` agent. Use the bundled Pier adapter for current Kimi
Code. A future Harbor adapter must be independently version-pinned and validated
before its results are called Kimi Code results.

## Kimi Code installation fails

Use an exact package version. For the Pier adapter, setup requires access to `raw.githubusercontent.com`, `github.com`, `nodejs.org`, and `registry.npmjs.org`. Inspect setup logs for the blocked hostname before changing the allowlist.

## `kimi: command not found`

The Node runtime or npm prefix is missing from `PATH`. The bundled Pier adapter sources `$HOME/.nvm/nvm.sh` and prepends `$HOME/.local/bin`. Do not switch to an unpinned installer with a different binary path during a scored run.

## Kimi installation exits 3 after nvm reports global packages

Some JavaScript task images already contain Node and global npm modules. nvm
0.40.2 can define the `nvm` function but return status 3 while its shell script
is sourced. Use the bundled source guard, which accepts the nonzero status only
when `command -v nvm` confirms the function loaded, then still pins Node 22 and
the requested Kimi Code version. Classify failures before agent startup as
infrastructure outcomes and recover them only in a separate job.

## A Kimi step retries only three times

Three is the Kimi Code 0.23.6 default. Override `loop_control.max_retries_per_step` deliberately through the Pier adapter kwarg or an equivalent pinned Kimi config. `loop_control.max_attempts_per_step` and `KIMI_LOOP_MAX_STEPS_PER_TURN` are not valid 0.23.6 controls.

## Container cannot reach a host-local endpoint

`127.0.0.1` inside the task container is the container itself. Relay the host loopback endpoint on the Docker bridge gateway, pass the bridge address as the model base URL, and allow that IP/host only during the agent phase. Validate from a disposable container.

## Relay logs contain credentials

Stop the verbose relay, restrict the log to owner-only permissions, and rotate exposed credentials. Resume with ordinary `socat` output or no traffic capture. Request bodies may also contain private task prompts and model reasoning.

## Model responses contain reasoning markup or malformed tools

Probe the endpoint directly with the selected protocol. Verify its reasoning parser, tool-call parser, content separation, finish reason, and long-context behavior before blaming the benchmark or adapter.

## Context window errors appear late in tasks

DeepSWE tasks are long-horizon. Align the server context limit and the agent's advertised context size. A short endpoint smoke does not validate long multi-turn context behavior.

## A full run stalls

Check the driver, task containers, agent logs, endpoint queues, tunnel health, free disk/inodes, Docker networks, memory, and I/O pressure. Reduce concurrency only after identifying endpoint or runner saturation. Preserve completed trial artifacts before recovery.

## Resume changes the score

Verify whether the backend skipped completed trials, retried errored trials, or replaced artifacts. Never use filtered resume on the immutable primary job because it deletes matching failed trial directories. Put approved infrastructure recoveries in a separate job and use the scorer's exact task/error allowlist overlay. Report the original result even when a corrected aggregate is also valid; a run with added attempts is not the original pass@1 trial set.

## A trajectory contains broken grammar or mixed words

Inspect the surrounding turns and the beginning, middle, and end of other
trajectories. Distinguish persistent grammatical or word-order corruption from
ordinary planning text, terse fragments, and malformed code or tool calls.
Report prevalence and whether the agent self-corrected. Trajectory quality is a
separate audit dimension and does not replace the verifier reward.
