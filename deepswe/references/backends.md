# Backend selection

## Pier

Pier is the canonical DeepSWE backend. The DeepSWE repository states that its leaderboard scores were produced with Pier, `mini-swe-agent`, and Modal. The Kimi Vendor Verifier DeepSWE section also explicitly names Pier.

Use Pier when:

- reproducing or comparing with a published DeepSWE result;
- following the KVV DeepSWE profile;
- using Pier trajectory and critique tooling;
- an existing run or artifact set was created by Pier.

Pier 0.3.0 supports the DeepSWE v1.1 separate verifier and collect-hook workflow. Its built-in agents do not include Kimi Code, so the optional adapter in `scripts/kimi_code_agent.py` registers it through `--agent-import-path`.

## Harbor

Harbor 0.20.0 supports the current DeepSWE task contract:

- `schema_version = "1.3"`;
- `verifier.environment_mode = "separate"`;
- `[[verifier.collect]]` patch generation;
- agent `no-network` baselines with run-specific `--allow-agent-host` additions;
- the prebuilt Docker images referenced by each task;
- a built-in `kimi-code` installed-agent adapter.

All 113 task configs at DeepSWE commit `435ee89ec2f2e2289f33b0da4f992f0b7b7266b9` load under Harbor commit `0348989adffbb43bf0b410fd36197333239633f1` (package 0.20.0).

Use Harbor when:

- the operator already uses Harbor;
- its built-in agent adapter is preferable to a custom Pier adapter;
- the result is explicitly reported as a Harbor run;
- strict reproduction of a Pier-produced score is not required.

## Known artifact warning

Current DeepSWE tasks declare `artifacts = ["/logs/artifacts/model.patch"]`. Harbor also injects `/logs/artifacts` as its conventional artifact directory, so it warns that the directory and child file overlap. The collect hook still writes the patch before artifact download, and the first directory claim contains `model.patch`.

Treat this specific warning as known upstream redundancy. Still require `model.patch` to exist in the collected artifacts and the verifier to produce a reward. A missing patch or reward is a failure, not a warning to suppress.

## Comparison policy

Record the backend as part of benchmark identity. Backend differences can change:

- agent installation and version resolution;
- network-policy application;
- prompt and adapter behavior;
- trajectory capture;
- retry and resume semantics;
- artifact layout and operational failure modes.

Use the same backend for a controlled model comparison. When changing backend, run matched oracle and agent smoke tests and describe the result as a cross-backend comparison.
