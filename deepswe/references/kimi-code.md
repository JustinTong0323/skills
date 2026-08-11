# Optional Kimi Code agent

Kimi Code is an optional DeepSWE agent. Do not make it a prerequisite for general DeepSWE runs.

## Choose the profile

- Current npm Kimi Code evaluation: use Pier with the bundled adapter.
- KVV DeepSWE profile: use Pier and Kimi Code 0.23.6 or newer because that is the published KVV contract.

Pin the package version. Do not use floating `latest` for a scored run.

## Harbor naming caveat

Harbor 0.20.0 exposes `--agent kimi-cli`, which installs the legacy Python
`kimi-cli` package. It does not expose the current npm
`@moonshot-ai/kimi-code` adapter described in this reference. Harbor remains a
valid DeepSWE backend for its compatible agents, but its `kimi-cli` results are
not interchangeable with Kimi Code results.

## Pier custom adapter

Pier commit `0daf53d` does not include Kimi Code. Put this skill's scripts directory on `PYTHONPATH` and register the bundled adapter:

```bash
export PYTHONPATH="<skill-dir>/scripts${PYTHONPATH:+:$PYTHONPATH}"

pier run -p deep-swe/tasks/abs-module-cache-flags \
  --agent-import-path kimi_code_agent:KimiCode \
  --model MODEL_ID \
  --agent-kwarg version=0.23.6 \
  --agent-env KIMI_MODEL_NAME=MODEL_ID \
  --agent-env KIMI_MODEL_API_KEY="$MODEL_API_KEY" \
  --agent-env KIMI_MODEL_BASE_URL="$MODEL_BASE_URL" \
  --agent-env KIMI_MODEL_PROVIDER_TYPE=openai \
  --job-name deepswe-kimi-smoke \
  --jobs-dir "$DEEPSWE_JOBS" \
  --yes
```

The adapter:

- rejects missing or pre-0.23.6 versions;
- installs the exact npm package into `$HOME/.local` and loads the pinned Node runtime;
- derives the runtime allowlist from `KIMI_MODEL_BASE_URL`;
- retains raw `stream-json` output and does not advertise incomplete ATIF support;
- supports optional `max_steps_per_turn` and `max_retries_per_step` kwargs through Kimi Code's `[loop_control]` config without replacing provider or model tables.

When an existing Kimi Code configuration already contains the provider and
model, prefer file injection so the API key does not appear in the runner's
process arguments:

```bash
chmod 600 "$HOME/.kimi-code/config.toml"

pier run -p deep-swe/tasks/abs-module-cache-flags \
  --agent-import-path kimi_code_agent:KimiCode \
  --model radixark/k3 \
  --agent-kwarg version=0.34.0 \
  --agent-kwarg config_file="$HOME/.kimi-code/config.toml" \
  --job-name deepswe-kimi-smoke \
  --jobs-dir "$DEEPSWE_JOBS" \
  --yes
```

The file must exist on the runner host and contain Kimi Code's normal
`providers` and `models` tables. The adapter copies it into the task container
with mode `0600`, derives endpoint hosts for the agent allowlist, and removes
the transfer copy. Keep the job directory owner-only because trajectories and
resolved metadata can still contain private prompts or endpoint details.

When loop-control kwargs are combined with `config_file`, the adapter parses the
host file, changes only the requested `[loop_control]` keys in a mode-0600
temporary copy, validates the merged TOML, and uploads that copy. The original
host file remains unchanged.

On a shared Docker host whose inotify quota is already exhausted, enable
container-local filesystem polling instead of changing host-wide sysctls:

```bash
--agent-kwarg filesystem_polling=true \
--agent-kwarg watch_poll_interval_ms=1000
```

Record this setting with the run. Polling avoids Kimi Code `EMFILE` watcher
failures but consumes more CPU, especially on large repositories.

Kimi Code 0.23.6 defaults to three retries after a failed step. There is no `KIMI_LOOP_MAX_STEPS_PER_TURN` setting. To override the real config fields with the Pier adapter:

```bash
--agent-kwarg max_steps_per_turn=500 \
--agent-kwarg max_retries_per_step=5
```

Omit `max_steps_per_turn` for no explicit limit. Treat retry changes as part of benchmark identity.

## Model environment

Common settings are:

| Variable | Purpose |
|---|---|
| `KIMI_MODEL_NAME` | Model ID sent to the endpoint |
| `KIMI_MODEL_API_KEY` | Bearer credential or endpoint-required placeholder |
| `KIMI_MODEL_BASE_URL` | Container-visible API base URL; required by the Pier adapter |
| `KIMI_MODEL_PROVIDER_TYPE` | `kimi` or compatible provider type |
| `KIMI_MODEL_MAX_CONTEXT_SIZE` | Context size advertised to Kimi Code |
| `KIMI_MODEL_CAPABILITIES` | Comma-separated model capabilities |
| `KIMI_MODEL_THINKING_EFFORT` | Model-supported thinking effort |
| `KIMI_MODEL_TEMPERATURE` | Optional temperature override |
| `KIMI_MODEL_TOP_P` | Optional top-p override |
| `KIMI_MODEL_MAX_COMPLETION_TOKENS` | Optional output-token limit |

Do not assume a context size, thinking effort, temperature, or top-p value is required by DeepSWE itself. Derive them from the selected model/profile and record them.

Record client overrides separately from effective server sampling. When
`KIMI_MODEL_TEMPERATURE` and `KIMI_MODEL_TOP_P` are absent, report that Kimi Code
omitted those fields; do not call an inferred value the observed request value.
The public Kimi K3 `generation_config.json` at revision `9f62e4e9` defines
`max_length` and `eos_token_id`, not temperature or top-p. Kimi's official K3
API contract fixes temperature at 1.0 and top-p at 0.95 and asks clients to omit
them, but a compatible third-party endpoint requires server configuration or
request-log evidence before claiming it applied that contract.

## Full-run validation profile

The completed 113-task local-Docker validation used Pier `0daf53d`, Kimi Code
0.34.0, filesystem polling at 1000 ms, four concurrent trials, and no explicit
loop or sampling overrides. The immutable primary job produced 67 reward-1, 41
reward-0, and five ungraded outcomes. A separate four-task recovery validated
the nvm source guard and raised the corrected strict result to 71/113 while one
verifier timeout remained zero. This validates the recorded profile, not a
universal Kimi Code sampling or concurrency requirement.

## KVV checks

Before calling a result KVV-compatible:

- use Pier;
- pin Kimi Code at 0.23.6 or newer;
- register it as the Pier agent, not as an out-of-band script;
- run the relevant KVV API preflight tests for the endpoint;
- record the KVV, Kimi Code, Pier, DeepSWE, adapter, and server revisions;
- retain evidence of the effective Kimi Code version and request behavior.
