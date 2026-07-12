---
name: swe-bench-pro
description: Run the public ScaleAI SWE-bench Pro benchmark against an OpenAI-compatible or hosted model endpoint with the official Scale evaluator and mini-SWE-agent scaffold. Use when a user asks to set up, smoke-test, run, resume, evaluate, troubleshoot, or compare SWE-bench Pro runs; generate Pro-compatible instances or patch files; use local Docker or Modal for Pro; or distinguish SWE-bench Pro from SWE-bench Verified.
---

# SWE-bench Pro Runner

Run inference with Scale's mini-SWE-agent fork and score patches with `scaleapi/SWE-bench_Pro-os`. Default to local Docker for both inference and evaluation. Use Modal when the user explicitly prefers cloud sandboxes or the local host cannot support the workload.

## Gather inputs

Obtain:

- API base URL and model name
- API key environment variable, if required
- thinking or non-thinking mode and model-specific request fields
- inference workers and evaluation workers
- run directory and run ID
- local Docker or Modal
- full dataset, slice, or instance filter

Do not put API keys in generated files or shell history. Pass them through an environment variable.

## Distinguish Pro from Verified

Do not use `princeton-nlp/SWE-bench_Verified`, `swebench.harness.run_evaluation`, `sb-cli`, or `swebench/sweb.eval.*` images for Pro.

Use:

- Dataset: `ScaleAI/SWE-bench_Pro`, split `test`
- Images: `jefzda/sweap-images:<dockerhub_tag>`
- Inference instances: YAML with `image_name`, `problem_statement`, `instance_id`, `base_commit`, and `repo_name: app`
- Patch schema: JSON list containing `instance_id`, `patch`, and `prefix`
- Evaluator: `SWE-bench_Pro-os/swe_bench_pro_eval.py`

Read [references/upstream.md](references/upstream.md) before changing revisions, image naming, prompt formatting, or evaluator commands.

## Set up

Choose a persistent root such as `/root/swe-bench-pro`. Run:

```bash
bash <skill-dir>/scripts/bootstrap.sh /root/swe-bench-pro
cd /root/swe-bench-pro/SWE-bench_Pro-os
source .venv/bin/activate
```

The bootstrap records repository revisions in `RUNNER_REVISIONS.txt`. Preserve that file with run artifacts.

The Pro repository's recorded mini-SWE-agent submodule revision may predate Pro support. The bootstrap deliberately fetches the Scale fork's `main` and verifies that `mini-extra run-batch` exists. Do not reset the submodule back to the recorded parent revision unless that revision contains `run-batch` and `config/swebp.yaml`.

## Prepare instances

Generate both the inference YAML and evaluator input from the same dataset snapshot:

```bash
python <skill-dir>/scripts/prepare_instances.py \
  --output-dir data/generated
```

This creates:

- `data/generated/instances.yaml`
- `data/generated/raw_samples.jsonl`
- `data/generated/dataset_manifest.json`

Keep the manifest with results. Confirm its count before a full run.

## Render the local-Docker config

Start from the official Pro prompt/config and add local-Docker runtime settings:

```bash
python <skill-dir>/scripts/render_config.py \
  --source mini-swe-agent/src/minisweagent/config/swebp.yaml \
  --output config/swebp-local.yaml \
  --command-timeout 180 \
  --container-timeout 12h \
  --completion-timeout 1200
```

Use `2400` seconds for very slow reasoning models. This timeout controls model completions; `command-timeout` controls shell actions inside task containers.

## Verify the endpoint

Check the model list and run one short completion. For reasoning models, derive accepted reasoning fields and values from the selected model's chat template, then verify that reasoning is separated from final content and that tool or response formatting matches the served model. Apply the same server-side parser, context-length, and concurrency checks used for a Verified run.

For an endpoint on the inference host, the mini-SWE-agent Python process calls the endpoint directly; task Docker containers do not need API access.

## Smoke test inference

Run one known instance before scaling:

```bash
mini-extra run-batch \
  --config config/swebp-local.yaml \
  --output-dir results/RUN_ID \
  --num-workers 1 \
  --source file \
  --instances-path data/generated/instances.yaml \
  --slice 0:1 \
  --no-shuffle \
  --environment-class docker \
  --model 'hosted_vllm/MODEL_NAME' \
  --model-api-base 'API_BASE' \
  --model-api-key "$OPENAI_API_KEY" \
  --model-temperature 0 \
  --per-instance-call-limit 250 \
  --per-instance-cost-limit 0
```

Use the LiteLLM provider prefix appropriate for the endpoint. `hosted_vllm/` is suitable for OpenAI-compatible SGLang/vLLM endpoints. Pass reasoning fields through `model.model_kwargs` in a run-specific config when needed.

Inspect the instance trajectory, `.pred` file, and `preds.json`. Require a non-empty git patch before proceeding.

## Run inference

Remove `--slice 0:1`, choose a new output directory, and raise workers conservatively. Keep inference workers below endpoint request capacity; begin near one quarter to one half of `max-running-requests` for long reasoning workloads. Watch both task completion count and server queues.

Resume by rerunning the identical command without `--redo-existing`. The runner uses completed trajectories to skip work. Use `--redo-existing` only for an intentional full rerun; for selected failures, move or delete only those instance artifacts and their `preds.json` entries.

Treat the driver process exit as the completion signal. A populated `preds.json` is not sufficient.

## Convert patches

Convert the run output to the Pro evaluator schema:

```bash
python <skill-dir>/scripts/convert_predictions.py \
  --input results/RUN_ID \
  --output results/RUN_ID/patches.json \
  --prefix RUN_ID \
  --expected data/generated/raw_samples.jsonl
```

The converter excludes empty patches, reports missing IDs, and rejects duplicates or unknown IDs. Missing predictions must still count as unresolved in the final strict score.

## Smoke test evaluation

Create a one-instance raw-sample JSONL matching the inference smoke-test instance, or pass a one-patch file and the full raw sample. Then run with one worker:

```bash
python swe_bench_pro_eval.py \
  --raw_sample_path data/generated/raw_samples.jsonl \
  --patch_path results/RUN_ID/patches.json \
  --output_dir results/RUN_ID/evaluation \
  --scripts_dir run_scripts \
  --num_workers 1 \
  --dockerhub_username jefzda \
  --use_local_docker
```

Add `--block_network` only when the selected repositories are known not to install dependencies during evaluation and the evaluation policy requires isolation. Add `--docker_platform linux/amd64` on ARM hosts.

## Evaluate the full run

Rerun the evaluator with an appropriate worker count. Start around 8-16 local workers and increase only if CPU, memory, disk, and Docker remain healthy. Use `--redo` only when intentionally replacing existing evaluator outputs.

The evaluator writes per-instance logs and `evaluation/eval_results.json`.

## Report strict results

Never report only the evaluator's printed accuracy. It divides by evaluated patches rather than the full expected set.

```bash
python <skill-dir>/scripts/summarize_results.py \
  --eval-results results/RUN_ID/evaluation/eval_results.json \
  --expected data/generated/raw_samples.jsonl \
  --predictions results/RUN_ID/patches.json
```

Report:

- resolved / expected and strict accuracy
- resolved / evaluated and submitted-patch accuracy
- expected, non-empty submitted, evaluated, unresolved, missing, and invalid-result counts
- dataset, runner, and evaluator revisions
- model, endpoint type, prompt/config, temperature/reasoning settings, call limit, workers, and backend

Use strict accuracy for comparisons and leaderboard-style claims.

## Modal path

Read the official `README.md`, `mini-swe-agent/swebench_pro.md`, `SWEAgent.Dockerfile`, and `justfile` at the recorded revision. Modal inference requires the Pro fork's SWE-ReX behavior and credentials from `modal setup`; follow the official container path rather than installing an arbitrary PyPI mini-SWE-agent. Evaluation defaults to Modal when `--use_local_docker` is omitted.

## Troubleshoot

Read [references/troubleshooting.md](references/troubleshooting.md) for submodule, image, partial-score, resume, Docker, Modal, and endpoint failure modes.
