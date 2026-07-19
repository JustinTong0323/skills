# Upstream contract

## Sources

- Benchmark and evaluator: `https://github.com/scaleapi/SWE-bench_Pro-os`
- Dataset: `https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro`
- Public leaderboard: `https://labs.scale.com/leaderboard/swe_bench_pro_public`
- Scale mini-SWE-agent fork: `https://github.com/scaleapi/mini-swe-agent`
- Scale SWE-agent fork: `https://github.com/scaleapi/SWE-agent`

Inspect these sources again before updating the skill because dataset size, run scripts, images, evaluator behavior, and fork revisions can change.

## Current public workflow

1. Load the `test` split from `ScaleAI/SWE-bench_Pro`.
2. Format each task's prompt from `problem_statement`, `requirements`, and `interface`.
3. Run an agent in the image named by `dockerhub_tag` under `jefzda/sweap-images`.
4. Collect a git patch per instance.
5. Convert patches to a JSON list with `instance_id`, `patch`, and `prefix`.
6. Run `swe_bench_pro_eval.py` with the dataset materialized as JSONL or CSV and the repository's `run_scripts` directory.
7. Score fail-to-pass plus pass-to-pass tests and treat missing instances as unresolved externally.

## Compatibility checks

Before accepting a mini-SWE-agent revision, require all of:

```bash
test -f mini-swe-agent/src/minisweagent/config/swebp.yaml
source .venv/bin/activate
mini-extra run-batch --help
```

The parent repository's submodule pointer and the Scale fork's `main` can differ. Record the exact parent, submodule, dataset fingerprint, generated manifest, and config with every run.

## Image naming

Prefer the dataset's `dockerhub_tag` directly:

```text
jefzda/sweap-images:<dockerhub_tag>
```

Do not reconstruct tags from `instance_id` when `dockerhub_tag` is available. Tag-generation helpers contain repository-specific normalization rules and can drift from the dataset.

## Evaluator semantics

The official evaluator:

- filters predictions to IDs present in the raw sample;
- evaluates only submitted patch records;
- writes `eval_results.json` as an instance-to-boolean mapping;
- prints `sum(results) / len(results)`.

Therefore its printed denominator is evaluated submissions, not necessarily every dataset instance. Always compute a strict score against the expected IDs from the same dataset snapshot.
