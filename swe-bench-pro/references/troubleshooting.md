# Troubleshooting

## `mini-extra` has no `run-batch`

The checked-out submodule predates SWE-bench Pro support. Fetch `origin/main` in `mini-swe-agent`, detach at that revision, reinstall editable, and record the SHA. Do not silently fall back to the regular `mini-extra swebench` command.

## `config/swebp.yaml` is missing

Use `mini-swe-agent/src/minisweagent/config/swebp.yaml` from the Scale fork revision that implements Pro. The ordinary SWE-bench config assumes `/testbed`, Princeton images, and Verified-style patch submission.

## Container starts in `/`

For local Docker, render the config with `environment.cwd: /app`. Pro images standardize the repository at `/app`. The Scale Pro agent collects its patch from `/app` as well.

## Inference container exits immediately

Pro images declare `/bin/bash` as their entrypoint. For mini-SWE-agent's local Docker environment, set `environment.run_args` to `["--rm", "--entrypoint", ""]` so its appended `sleep <container-timeout>` runs directly.

## Patch contains image-generated data

Some Pro images start with untracked runtime files such as NodeBB's `appendonlydir/`. Before the agent runs, add the image's existing untracked paths to `/app/.git/info/exclude`. Do not run `git clean`; runtime data may be required. The rendered local config performs this baseline exclusion through `run.env_startup_command`.

## Image pull or tag fails

Confirm the instance YAML uses the dataset's `dockerhub_tag` verbatim under `jefzda/sweap-images`. Test one image with `docker pull`. On ARM, use `linux/amd64` for evaluation; inference support depends on the runtime.

## Evaluation says zero matching patches

Compare IDs in `patches.json` with `raw_samples.jsonl`. Pro IDs start with `instance_` and contain version suffixes. Do not use Verified IDs or rename folders.

## Accuracy is unexpectedly high after failures

The evaluator scored only submitted non-empty patches. Run `summarize_results.py` and use resolved divided by the full expected count. Report unresolved, missing, and invalid-result counts.

## Empty patches

Inspect the trajectory exit status and final `.pred` file. Common causes are a step/cost limit, malformed response format, wrong working directory, endpoint timeout, context overflow, or the agent declaring completion before editing. Fix the cause and rerun only those instances.

## Resume skips a failed instance

The batch runner keys resume behavior from per-instance trajectory artifacts. Remove or move only the failed instance directory and its entry in `preds.json`, then rerun the same command without changing the output directory. Preserve old artifacts elsewhere for auditability.

## Local evaluation cannot reach Docker

Verify `docker version`, `docker run --rm hello-world`, free disk, and daemon permissions. Reduce `--num_workers` when containers exit, the daemon stalls, or memory pressure rises.

## Modal fails during environment startup

Run `modal setup`, verify `~/.modal.toml`, use the official Pro container and patched SWE-ReX path, and raise deployment startup timeout. Record Modal and fork revisions.

## Endpoint queue collapses

Reduce inference workers. Long-horizon Pro tasks maintain long conversations, so a worker count that succeeds for short serving benchmarks can saturate KV cache or hit request timeouts here.

## `reasoning_effort` returns HTTP 400

Check the selected model's chat template and request schema instead of assuming a framework-wide value. Accepted values are model-specific even on the same SGLang version. For the GLM-5.2 smoke run, the model accepted `none`, `low`, `medium`, `high`, or `max` and rejected `no_think`; do not generalize that set to other models.

## Re-evaluation reuses old outputs

The evaluator skips existing per-instance output unless `--redo` is passed. Use a new evaluation directory for a new patch set, or pass `--redo` intentionally and retain the old directory separately.

## Evaluation stalls during dependency installation

Some Pro run scripts install dependencies, including NodeBB's `npm install` step. Do not pass `--block_network` for those instances. Network blocking is an opt-in policy check, not a safe benchmark-wide default.
