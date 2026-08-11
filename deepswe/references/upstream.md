# Upstream contract

## Sources

- DeepSWE corpus and run guide: `https://github.com/datacurve-ai/deep-swe`
- DeepSWE site: `https://deepswe.datacurve.ai/`
- Pier: `https://github.com/datacurve-ai/pier`
- Harbor task documentation: `https://www.harborframework.com/docs/tasks`
- Harbor: `https://github.com/harbor-framework/harbor`
- Kimi Vendor Verifier: `https://github.com/MoonshotAI/Kimi-Vendor-Verifier`
- Kimi Code: `https://github.com/MoonshotAI/kimi-code`
- Kimi K3 generation config: `https://huggingface.co/moonshotai/Kimi-K3/blob/9f62e4e9fffbd0a83ddd60e1c209d828994b3569/generation_config.json`

Inspect these sources again before updating versions, task-count claims, backend support, adapter behavior, or a reproduction recipe.

## Validated revision matrix

| Component | Validated revision |
|---|---|
| DeepSWE | `435ee89ec2f2e2289f33b0da4f992f0b7b7266b9` |
| Harbor | 0.20.0 / `0348989adffbb43bf0b410fd36197333239633f1` |
| Pier | 0.3.0 / `0daf53d3599e58c4506cf0bcff5e12c77dc282d2` |
| Minimum KVV Kimi Code | 0.23.6 / tag `@moonshot-ai/kimi-code@0.23.6` |
| Full-run Kimi Code | 0.34.0 |

The matrix records compatibility checked while authoring this skill. It is not a request to silently pin every future comparison to these revisions. When intentionally upgrading, record the new revisions and re-run the task audit, oracle smoke, and real-agent smoke.

The 113-task Kimi Code 0.34.0 validation used Pier `0daf53d`, local Docker,
filesystem polling at 1000 ms, concurrency 4, and no loop or sampling overrides.
The primary job stayed immutable. A separate four-task infrastructure recovery
validated the adapter's nvm source guard; the newer TOML merge path is covered
by focused tests and still requires the real-agent smoke gate before a scored
run.

## Task contract

The validated DeepSWE corpus contains 113 task directories. Each task must have:

- `task.toml` and `instruction.md`;
- a prebuilt Docker image;
- agent network mode `no-network`;
- verifier environment mode `separate`;
- a verifier collect hook that writes `/logs/artifacts/model.patch`;
- `tests/test.sh` and verifier material;
- a binary `reward` in the verifier result.

The agent edits and may commit inside the agent environment. The collect hook creates a binary git diff against the task's base commit. Harbor/Pier transfers that artifact to a fresh verifier environment, where the patch and held-out test patch are applied and graded.

## Canonical result scope

DeepSWE's public documentation says published leaderboard scores were produced with Pier running `mini-swe-agent` on Modal. A result using Harbor, another agent, local Docker, a different retry policy, or a changed timeout is useful but is not a strict reproduction.

The KVV DeepSWE section specifies Pier and Kimi Code 0.23.6 or newer. It does not define a universal DeepSWE sampling profile. Record client-sent request settings separately from server defaults instead of importing temperature or top-p values from another KVV benchmark table or from model configuration fields that are not the API contract.
