# Harbor harnesses for Terminal-Bench

## Comparison matrix

| Property | Terminus-2 | Claude Code | Pi |
|---|---|---|---|
| Harbor agent | `terminus-2` | `claude-code` | `pi` |
| Endpoint | OpenAI-compatible `/v1` | Anthropic-compatible server root | OpenAI-compatible `/v1` |
| Model prefix | `openai/` | none | custom Pi provider, such as `sglang/` |
| Explicit temperature / top-p | yes | no | no in stock Harbor adapter |
| Reasoning control | agent kwarg | agent kwarg | Pi thinking level mapped in `models.json` |
| Extra file | none | none | mounted `/root/.pi/agent/models.json` |
| Main compatibility surface | LiteLLM/OpenAI | Anthropic Messages and Claude tools | Pi CLI and models registry |

The three harnesses differ in system prompt, context management, tool protocol, command execution, sampling, retries inside the agent, and termination behavior. Keep the model server and Harbor task configuration fixed, but describe the result as a harness A/B rather than a pure model A/B.

## Terminus-2

Use the OpenAI `/v1` base. The renderer produces the equivalent of:

```json
{
  "name": "terminus-2",
  "model_name": "openai/MODEL_ID",
  "env": {
    "OPENAI_API_KEY": "EMPTY"
  },
  "kwargs": {
    "api_base": "http://SERVER:30000/v1",
    "reasoning_effort": "max",
    "temperature": 1.0,
    "interleaved_thinking": true,
    "model_info": {
      "max_input_tokens": 1048576,
      "max_output_tokens": 393216,
      "input_cost_per_token": 0.0,
      "output_cost_per_token": 0.0
    },
    "llm_call_kwargs": {
      "max_tokens": 393216,
      "top_p": 0.95
    }
  }
}
```

Replace model limits with the served model's actual values. Terminus-2 is the closest of these stock Harbor agents to a controlled temperature/top-p experiment. The renderer requires a finite non-negative temperature and `0 < top_p <= 1`.

Terminus-2 performs model requests in the Harbor launcher process through LiteLLM. The agent config `env` reaches container-side execs but does not supply credentials to that host-side client. For an authenticated endpoint, load `OPENAI_API_KEY` into the launcher environment from an owner-only file before `harbor run`; do not rely on `agents[].env.OPENAI_API_KEY` or put the key literal in shell history. One complete campaign's first smoke failed before any model request until the launcher environment was corrected.

## Claude Code

Use the Anthropic server root, not the OpenAI `/v1` base:

```json
{
  "name": "claude-code",
  "model_name": "MODEL_ID",
  "kwargs": {
    "reasoning_effort": "max",
    "max_thinking_tokens": 393216
  },
  "env": {
    "ANTHROPIC_BASE_URL": "http://SERVER:30000",
    "ANTHROPIC_API_KEY": "EMPTY",
    "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "393216"
  }
}
```

Claude Code owns its sampling behavior and may cap effective context or output below the environment values. `CLAUDE_CODE_ATTRIBUTION_HEADER=0` avoids changing otherwise stable conversation prefixes and is required for comparable prefix-cache behavior. Record the CLI version and effective limits from the trajectory. Its `Read` tool can emit Anthropic `document` blocks for PDFs; the server must support those blocks or the run can fail with HTTP 400.

Do not assume `reasoning_effort=max` produces only `max` requests. Agent versions can emit related values such as `high` during the same run. Probe every value observed in a smoke against the live Anthropic endpoint, inspect server logs for template or validation errors, and repeat the probe at intended concurrency before a large run.

Agent setup downloads the Claude CLI inside task environments. A setup download reset is infrastructure failure; retry only the affected setup in a new auditable job or through an explicitly scoped infrastructure retry policy.

## Pi

The Harbor Pi adapter does not generate a custom provider registry. The renderer creates an owner-only, content-addressed model file and mounts it into every task:

```json
{
  "type": "bind",
  "source": "/home/ubuntu/tb21/pi/models.sha256-DIGEST.json",
  "target": "/root/.pi/agent/models.json",
  "read_only": true
}
```

The renderer writes a provider with `api: openai-completions`, the OpenAI `/v1` base, a reasoning-effort mapping, context and output limits, zero local cost, and reasoning replay support. `--pi-models-path` is a base filename: the mounted source adds a semantic SHA-256 that excludes the credential value, and the same identity cannot be overwritten with different content. Keep it distinct from `--output`. The job config records the semantic digest; archive the resolved source file named in the mount and verify its exact file hash. Use:

```text
--agent pi
--model MODEL_ID
--pi-thinking xhigh
--pi-models-path /home/ubuntu/tb21/pi/models.json
```

The renderer sets `PI_OFFLINE=1` and `PI_SKIP_VERSION_CHECK=1` in the agent environment.

Stock Harbor 0.20.0 passes the full task instruction as the Pi process's positional argv. Two consequences were confirmed on TB2.1:

- an instruction beginning with `- ` is parsed as a Pi option because the adapter omits an option terminator;
- a pattern kill such as `pkill -f <pattern>` can kill Pi itself when the pattern appears in the instruction stored in its argv.

Both share one cause: the instruction is in argv. Pi's CLI accepts file arguments (`pi [options] [@files...] [messages...]`), so writing the instruction to a container-local file and passing `@/path` removes it from argv entirely and fixes both bugs at once. With no instruction token in argv there is nothing for a leading `- ` to be misparsed as, and nothing for a pattern kill to match. Confirmed by intervention on the deterministic case: a task that failed 3 of 3 stock attempts with an identical `NonZeroAgentExitCodeError` passed on the first patched attempt.

Rules for using the remedy:

- Never patch the adapter inside a scored run. Finish the stock job, then rerun the affected tasks as a separate, explicitly labelled job.
- A patched-adapter result is not a stock-Pi number. Do not fold it into a stock union or place it beside a published Terminal-Bench figure. It answers one question: do these tasks become evaluable once the harness stops killing itself?
- Ship the patch with a revert path and record which adapter each job ran.
- Prove the failure mechanism is present in the control arm before believing a negative result. One argv-versus-file comparison was invalid because `procps` was absent from the container, so the pattern-kill binary did not exist and both arms survived. Both arms passing is the wrong shape for a real effect; treat it as a broken experiment rather than a fixed bug.

Upgrading Harbor does not help. Harbor 0.20.0 and 0.21.0 build this command identically in `harbor/agents/installed/pi.py`, which passes the instruction through `shlex.quote` and interpolates it into the argv string, and the community `badlogic/pi-terminal-bench` adapter carries the same construction. The same self-termination bug class is reported open in other agent CLIs, where prompt-level mitigation alone has not held.

## Sampling and official comparisons

Pin what each harness can actually control:

- Terminus-2: reasoning effort, temperature, top-p, maximum tokens.
- Claude Code: reasoning effort and nominal thinking/output limits; no matched temperature/top-p knobs.
- Pi: thinking-level mapping; no stock Harbor temperature/top-p knobs.

Do not claim that two runs isolate the agent harness if the API protocol, sampling, context limit, timeout policy, or server launch also changed.

Treat reasoning-effort names as versioned prompt profiles, not universal ordinal settings. The same label can map to different injected text across a model revision, server profile, or harness release, while a different label may preserve a legacy prompt. Probe the effective request or rendered prompt, record the mapping, and compare prompt profiles before interpreting `low`, `high`, or `max` scores.
