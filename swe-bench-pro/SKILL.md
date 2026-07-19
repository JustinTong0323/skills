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
- number of independent runs and per-run seeds for Pass@k
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

## Protect the runner

Prefer a dedicated runner for a full Pro run. Before reusing a host, inspect active benchmark processes, containers, disk consumers, and existing result directories. If another benchmark is in progress, acquire another machine unless both jobs have explicitly partitioned disk, Docker resources, ports, and result paths. Never prune containers, images, or files that belong to another run.

Check capacity before pulling or evaluating:

```bash
df -h /
docker system df
free -h
nproc
ps -eo pid,stat,cmd | grep -E '[m]ini-extra|[s]we_bench_pro_eval'
```

Pro images and evaluator outputs can consume hundreds of GB. Leave enough headroom for cold pulls, extracted image layers, per-instance outputs, and retry logs. If the estimate is uncertain or the volume is already busy, use a large-volume runner rather than relying on cleanup during a live run.

## Set up

Choose a persistent root such as `/root/swe-bench-pro`. Run:

```bash
bash <skill-dir>/scripts/bootstrap.sh /root/swe-bench-pro
cd /root/swe-bench-pro/SWE-bench_Pro-os
source .venv/bin/activate
```

The bootstrap records repository revisions in `RUNNER_REVISIONS.txt`. Preserve that file with run artifacts.

The Pro repository's recorded mini-SWE-agent submodule revision may predate Pro support. The bootstrap deliberately fetches the Scale fork's `main` and verifies that `mini-extra run-batch` exists. Do not reset the submodule back to the recorded parent revision unless that revision contains `run-batch` and `config/swebp.yaml`.

For local Docker, require an authenticated Docker Hub session on the runner before pulling images or starting a full run. Explicitly ask the user to run `docker login` interactively on that host when authentication has not been verified; do not request or handle their password or token. Continue only after the user confirms login and the runner's Docker config contains Docker Hub credentials. An already-cached image does not prove that later pulls are authenticated.

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

Reasoning controls are model-specific, not SGLang-version-specific. Do not infer that `none`, `no_think`, `thinking`, or another value is valid because it worked for a different model on the same server version.

For long reasoning workloads, remove MTP/EAGLE speculative decoding unless a representative Pro smoke test proves a gain. Tune chunked prefill for the intended batch size instead of copying a small-batch value. A stable GLM-5.2-NVFP4 reference on four large-memory GPUs used `--chunked-prefill-size 32768`, `--max-prefill-tokens 32768`, and no speculative-decoding flags; treat this as a measured reference, not a universal default.

When the endpoint is reached through a tunnel or port forward, verify both ends before every run and during monitoring:

```bash
curl -fsS --max-time 5 http://127.0.0.1:FORWARDED_PORT/health
curl -fsS --max-time 5 http://SERVER_HOST:SERVER_PORT/health
```

Run the forward under a persistent supervisor and record its PID or session. If it dies, restore the forward and confirm `/models` plus one completion before resuming failed instances.

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

For Pass@k, render one config per run with a distinct seed and a distinct output directory. Keep prompt, model, temperature, reasoning settings, limits, and dataset manifest identical. Stagger run starts on a single runner so cold image pulls do not stampede Docker Hub and the local disk.

After each driver exits, reconcile artifacts instead of trusting the wrapper return code alone. Require the expected number of unique trajectory and prediction IDs and inspect exit statuses for environment-startup failures. A terminal-rendering exception can occur after all trajectories were written, while a clean-looking wrapper can still leave failed environments. Delete only the failed environment artifacts and their `preds.json` entries, then resume the same run.

Do not disable reasoning merely because progress is slow. First check completed-trajectory growth, endpoint queues, tunnel health, GPU utilization, Docker pulls, disk I/O pressure, and tasks near their container timeout.

## Monitor a long run

Check at a fixed interval and report only material changes. Track:

- trajectories and predictions against the expected manifest count for every run
- driver/finalizer exit markers and active processes
- environment failures eligible for targeted retry
- endpoint and forwarded-port health
- running containers, free disk, and Docker disk usage
- D-state process count and `/proc/pressure/io`
- evaluation output count, merged-result count, retry errors, and strict summaries

Do not interpret historical retry errors as current failure after coverage is complete. Final output coverage and the latest process state are authoritative.

## Convert patches

Convert the run output to the Pro evaluator schema:

```bash
python <skill-dir>/scripts/convert_predictions.py \
  --input results/RUN_ID \
  --output results/RUN_ID/patches.json \
  --prefix RUN_ID \
  --expected data/generated/raw_samples.jsonl
```

The converter excludes empty patches, reports missing prediction records separately from empty patches, and rejects duplicates or unknown IDs. `missing_or_empty` is the total strict-score penalty before evaluation. Both missing and empty predictions must count as unresolved in the final strict score.

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

For a cold full-dataset evaluation, prefer resumable chunks such as 32 expected IDs. Keep the full raw sample file, slice `patches.json` by those IDs, and reuse one persistent evaluation directory. Start the first cold-cache run around 4 workers; after its images are cached, 8-16 workers is usually safer for later runs. After every chunk:

1. require exit code zero;
2. verify one `<prefix>_output.json` exists for every submitted patch in the chunk;
3. merge the chunk's `eval_results.json` into a persistent merged mapping;
4. record the completed expected-ID offset;
5. prune only images owned by this run when disk pressure requires it.

Never delete the evaluation directory when resuming. Restart from the last verified offset and let existing per-instance outputs remain available. The official evaluator may overwrite its top-level result mapping for the current invocation, so preserve and validate the merged mapping yourself.

Docker SDK clients default to a short HTTP timeout that can fail while a large image is still pulling. If logs show a 60-second `ReadTimeout` during cold pulls, use a recorded local evaluator patch that changes `docker.from_env()` to `docker.from_env(timeout=1800)`, then retry the same chunk. Do not classify a timed-out pull as a model failure.

If the runner accumulates many D-state processes or sustained writeback pressure, stop launching new chunks, preserve result directories and the last verified offset, then recover the host. After recovery, verify filesystem health, Docker, and result coverage before resuming.

## Report strict results

Never report only the evaluator's printed accuracy. It divides by evaluated patches rather than the full expected set.

```bash
python <skill-dir>/scripts/summarize_results.py \
  --eval-results results/RUN_ID/evaluation/eval_results.json \
  --expected data/generated/raw_samples.jsonl \
  --predictions results/RUN_ID/patches.json \
  --require-complete-submitted
```

Report:

- resolved / expected and strict accuracy
- resolved / evaluated and submitted-patch accuracy
- expected, non-empty submitted, evaluated, unresolved, missing, and invalid-result counts
- dataset, runner, and evaluator revisions
- model, endpoint type, prompt/config, temperature/reasoning settings, call limit, workers, and backend

Use strict accuracy for comparisons and leaderboard-style claims.

## Report empirical Pass@k

After all runs pass the single-run coverage gate, aggregate them with:

```bash
python <skill-dir>/scripts/summarize_pass_at_k.py \
  --expected data/generated/raw_samples.jsonl \
  --run results/RUN_1 \
  --run results/RUN_2 \
  --run results/RUN_3 \
  --run results/RUN_4 \
  --output results/MODEL-pass-at-4.json
```

The aggregator requires distinct run directories and exactly one boolean evaluation result for every non-empty submitted patch. It reports each strict score, the resolved union, empirical Pass@k, and the number of expected instances resolved in exactly `0..k` runs. The histogram must sum to the expected dataset count.

Call this metric empirical Pass@k: it is the observed union across these exact independent runs, not an extrapolated estimator for an unobserved sampling budget.

## Completion gate

Before declaring a full run complete, verify all of:

1. The dataset manifest count and unique IDs match every run's trajectories and prediction records.
2. No environment-startup failures remain after targeted retries.
3. Conversion summaries distinguish missing records, empty patches, and non-empty patches.
4. Evaluation output count equals the non-empty patch count for every run.
5. Evaluation-result keys exactly equal submitted non-empty patch IDs and all values are boolean.
6. Strict summaries use the full expected denominator and report zero invalid results.
7. The Pass@k histogram sums to the expected count and an independent union recomputation matches the report.
8. Drivers, evaluators, and finalizers have exited; their final exit markers are zero.
9. Endpoint, forward, Docker, filesystem, and disk state are recorded in the handoff or report.

Keep dataset manifests, runner revisions, configs, conversion summaries, evaluation results, strict summaries, Pass@k output, and relevant retry logs together as the experiment artifact set.

## Modal path

Read the official `README.md`, `mini-swe-agent/swebench_pro.md`, `SWEAgent.Dockerfile`, and `justfile` at the recorded revision. Modal inference requires the Pro fork's SWE-ReX behavior and credentials from `modal setup`; follow the official container path rather than installing an arbitrary PyPI mini-SWE-agent. Evaluation defaults to Modal when `--use_local_docker` is omitted.

## Troubleshoot

Read [references/troubleshooting.md](references/troubleshooting.md) for submodule, image, partial-score, resume, Docker, Modal, and endpoint failure modes.
