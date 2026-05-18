---
name: sglang-ci-monitor
description: >-
  Check, diagnose, rerun, and wait on CI for a sglang PR. Use whenever the user asks about CI on a sglang PR, wants to monitor a run, diagnose a CI failure, rerun failed jobs, or wait for CI to finish — even when they phrase it as "is the PR green?", "what's happening with the tests?", "something failed, look at it", or "babysit this PR for me". Assumes the user is a sglang CI maintainer, so all slash commands are available. Sharp edges: fast-fail cascades that hide the real root, `/rerun-failed-ci` vs `/tag-and-rerun-ci` vs `/rerun-test` vs `/rerun-group` doing subtly different things, `/rerun-stage` is **deprecated** (just posts a notice — does nothing), `pr-test.yml` (base CI, `base-a/b/c` stages) vs `pr-test-extra.yml` (label-gated extra CI, `extra-a/b` stages) being two separate runs, issue_comment-based reruns invisible on the PR page, exit-255 ambiguity — this skill is the default playbook.
---

# sglang CI monitor

The correct way to work with `sgl-project/sglang` CI. Naive moves (`gh run rerun`, `sleep && tail`, reading the PR page) look right and waste hours. Follow the patterns here.

All `gh` commands pass `--repo sgl-project/sglang` explicitly because the active worktree is usually a fork.

## Execution policy — act, don't ask

When this skill triggers, the user's ask ("monitor / diagnose / rerun / babysit") is the approval. Run the whole flow in one pass. Do **not** stop mid-flow to re-confirm.

Pre-approved (just do them, with one sentence of narration) — **on any sgl-project/sglang PR, regardless of author**. The user is a CI maintainer with permissions across the whole repo:

- All read-only `gh` queries (`gh pr view`, `gh api .../runs|jobs|annotations|logs`).
- Posting slash commands: `/rerun-failed-ci`, `/tag-and-rerun-ci`, `/rerun-test`, `/rerun-group`, `/tag-run-ci-label`. Adding the `run-ci-extra` label to opt the PR into `pr-test-extra.yml` coverage (or `bypass-fastfail` / `bypass-maintenance` when the diagnostic situation warrants it — see §3).
- `gh run cancel` as the precursor to a rerun.
- `CronCreate` for polling.

Do **not** post `/rerun-stage` — it's deprecated (PR #25322). The handler now replies with a `-1` reaction and a deprecation comment, and does nothing else; posting it costs the user a notification for no result.

Stop and ask only when:

- The action is off the pre-approved list (force-push, close, delete branch, edit labels outside the slash flow).
- The next step is a **code change** in response to a real failure — diagnosis is autonomous, fixing is not.

Do **not** pause to ask "PR isn't yours, ok to proceed?" — the user has explicitly waived that check.

## 1. Start the flow

### Scope: NVIDIA only

Only monitor NVIDIA CI. **Filter out AMD jobs entirely** — anything matching `*-amd*` is out of scope. Examples to filter: `stage-{a,b,c}-test-*-amd*` (AMD still uses the old `stage-*` prefix — only the NVIDIA workflow was renamed to `base-*`), `sgl-kernel-unit-test*-amd`, `multimodal-gen-test-*-amd*`, the `PR Test (AMD)` and `PR Test ROCm 7.2 (AMD)` workflows.

- When pulling the rollup, exclude AMD jobs before splitting failures into root vs. cascade.
- Don't report AMD failures, don't diagnose them, don't rerun them.
- Cron polling considers CI "terminal" when every **non-AMD** check is terminal — ignore AMD pending state.
- AMD has no slash-command rerun — its stage-level dispatch lives only in the Actions UI (`PR Test (AMD)` → *Run workflow* → pick a stage). The deprecated `/rerun-stage` was NVIDIA-only and doesn't apply to AMD either.

### Resolve the PR

If the user didn't name a PR:

```bash
gh pr view --json number,author,headRefName --head "$(git branch --show-current)"
```

If no PR is associated, ask once for a number or URL. Otherwise proceed. Do not gate on PR ownership — author check is intentionally skipped (see Execution policy).

### Pull job-level status

The PR page's Checks list and `gh pr checks` both miss base-c jobs. Always use `statusCheckRollup`:

```bash
gh pr view <PR> --repo sgl-project/sglang --json statusCheckRollup,labels,body \
  --jq '{labels: .labels | map(.name),
         checks: .statusCheckRollup | group_by(.conclusion)
                 | map({conclusion: .[0].conclusion, count: length})}'
```

### Fast `<RUN_ID>` shortcut — read the PR States block

`pr-states.yml` maintains a CI States block at the bottom of every PR body, between `<!-- pr-states:start -->` and `<!-- pr-states:end -->`. It shows run links for both `pr-test.yml` (base CI) and `pr-test-extra.yml` (extra CI) when the block is current. This is usually the cheapest way to grab an initial `<RUN_ID>`, but treat it as a shortcut, not the final source of truth after slash-command reruns — workflow-run refresh wiring can lag or drift from workflow names.

```bash
gh pr view <PR> --repo sgl-project/sglang --json body \
  --jq '.body' | sed -n '/<!-- pr-states:start -->/,/<!-- pr-states:end -->/p'
```

You'll see something like `Latest PR Test (Base): [Run #12345678](https://github.com/.../actions/runs/12345678)` and a matching `(Extra):` line — or `:x: Missing 'run-ci' label` / `:warning: Not enabled — add 'run-ci-extra' label to opt in` when a workflow is gated off. Reach for `gh run list --workflow=pr-test.yml --branch <head> --limit 1 --repo sgl-project/sglang` when the block is missing, after a slash-command rerun, when the link looks stale, or when you need a non-latest run.

### Always separate real failures from fast-fail victims

sgl CI uses aggressive fast-fail (`.github/actions/check-pr-test-health`; previously `check-stage-health` — same behavior, renamed in the `stage-*` → `base-*` rework). When any earlier job fails, the health check inside every downstream job calls `core.setFailed('Fast-fail: skipping — root cause job(s): <names>')`. A raw count of FAILURE in the rollup is misleading — "8 FAILURE" often means "1 root + 7 cascade victims".

Two label-driven exceptions to know about:

- **`bypass-fastfail` label on the PR** — the health check skips its jobs-failed scan and returns success. The wait-for-base-* jobs also short-circuit to success, so every stage dispatches in parallel. Cascades disappear; every downstream FAILURE in the rollup is a real failure, not a victim. The lint cascade still fires (see Case B) — only the jobs-failed half is bypassed.
- **`base-c-test-8-gpu-h20` is excluded from the root-cause set.** Its failures are treated as flaky-h20 noise and do **not** trigger fast-fail in other jobs. So if h20 is the only FAILURE you see, no cascade will appear — but the job itself is still a real (flaky) failure to diagnose or rerun.

**The authoritative way: read the annotation** on any fast-fail job. It names the real roots directly:

```bash
gh api repos/sgl-project/sglang/check-runs/<JOB_ID>/annotations --jq '.[].message'
# → "Fast-fail: skipping — root cause job(s): base-b-test-1-gpu-large, base-c-test-4-gpu-h100"
# or → "Fast-fail: lint check failed"   (if lint.yml itself is red — checked first, takes priority)
```

The two annotation shapes are **mutually exclusive**: the health check inspects lint first and returns early if it failed, so you'll see one or the other, never both in the same message.

Pick any FAILURE job whose duration is near-instant, read its annotation, and every other victim gives the same names. Then diagnose those roots only.

**Fallback** (cheap overview when annotation isn't needed): split by duration — real jobs almost never complete in <15s, fast-fail skips almost always do:

```bash
gh api repos/sgl-project/sglang/actions/runs/<RUN_ID>/jobs --paginate --jq '
  .jobs
  | map(select(.conclusion == "failure"))
  | map(. + {dur: ((.completed_at | fromdate) - (.started_at | fromdate))})
  | group_by(.dur < 15)
  | map({bucket: (if .[0].dur < 15 then "fast_fail_victim" else "real_failure" end),
         count: length,
         jobs: map({name, dur, html_url})})'
```

Note: a lint failure in the separate `lint.yml` workflow also triggers the fast-fail across the whole pr-test run — check `lint` in the rollup before blaming pr-test.

`<RUN_ID>` is the `pr-test.yml` run. Easiest source: the PR States block (see above). Otherwise pick it from any non-gate rollup check's `detailsUrl`, or `gh run list --workflow=pr-test.yml --branch <head> --limit 1 --repo sgl-project/sglang`. For extra CI (`run-ci-extra` PRs), substitute `--workflow=pr-test-extra.yml` — it's a separate workflow with its own run id.

### Report style

Lead with the conclusion, with cascades collapsed. Counts first, prose second.

Good:

> 1 real failure (`base-a-test-cpu` partition 2, exit 255, 4m18s), 7 fast-fail victims, 12 SUCCESS. Diagnosing the real one now.

> 2 labels (no `run-ci`), pr-gate denied, 50 SKIPPED. CI never fired. Posting `/tag-and-rerun-ci` — just tagging won't retrigger; pr-test doesn't refire on `labeled`.

Bad:

> I ran `gh pr view` to check the status. Looking at the output, it seems that there are 8 failures. Let me investigate further…

Don't narrate queries. Don't report raw FAILURE counts without first collapsing cascades. Don't ask what to do next when the flow already dictates it.

## 2. Diagnose failures

§1 gives you the roots (from annotations, or the duration fallback). Match your situation to a case below. Most sessions are Case A, C, or D; the rest are rarer.

### Case A: nothing is running (CI never fired)

Symptom (from `pr-gate.yml`): pr-gate exits with `Missing required label 'run-ci'` and every downstream job is SKIPPED. The rollup shows no `run-ci` label, pr-gate FAILUREs, then a sea of SKIPPED. The PR States block will say `:x: Missing run-ci label`.

Action: `/tag-and-rerun-ci` (adds label and reruns in one shot). No further diagnosis — nothing actually ran yet.

**Related: `pr-test-extra.yml` (the extra CI) is double-gated** — it requires both `run-ci` AND `run-ci-extra`. So a PR can be green on base CI but show `:warning: Not enabled` for extra CI if `run-ci-extra` is absent. That's intentional, not a failure to fix. Only opt in when the user actually wants the nightly-class coverage; merely waiting on extra CI without the label will never become green.

### Case B: lint failed → everything fast-fails

Symptom: `lint` check FAILURE in the rollup; every pr-test job annotation is `Fast-fail: lint check failed`.

Action: fix lint first. No pr-test diagnosis until lint is green — it'll keep fast-failing the whole run regardless.

### Case C: fast-fail cascade from a pr-test root

Symptom: `Fast-fail: skipping — root cause job(s): A, B` in victim annotations. Diagnose only A, B. The victims resolve automatically once roots turn green.

### Case D: real failure — read annotation, then log

```bash
gh api repos/sgl-project/sglang/check-runs/<JOB_ID>/annotations --jq '.[].message'
gh api repos/sgl-project/sglang/actions/jobs/<JOB_ID>/logs
```

Search logs for `FAILED:` to locate the test file and traceback.

### Case E: only victims, no root in the current run

The root lives in a sibling workflow — common candidates: `Build Wheel` from `_pr-test-sgl-kernel-build.yml`, JIT-kernel / multimodal-gen test workflows, or `pr-test-extra.yml` when extra CI failed. `rerun_failed_jobs` is scoped to a single run, so the failure won't be retried by `/rerun-failed-ci` unless you look at the right run:

```bash
gh run list --branch <head> --limit 5 --repo sgl-project/sglang
```

Note that `/rerun-failed-ci` iterates every workflow run on the head SHA with conclusion `failure` or `skipped` (not just `pr-test.yml`), so a failed sibling workflow IS picked up — but you still need to look at its run to read the actual log. Diagnose the parent run's failure directly.

### Exit code quick reference (for Case D log-reading)

| Code | Meaning | Treat as |
|---|---|---|
| 0 | pass | — |
| 1 | generic test failure | real |
| 128 | runner killed before test ran | infra → rerun |
| 143 | SIGTERM / cancelled | infra → rerun if unexpected |
| 255 | CI wrapper exit when pytest/script returned 1 | **real** — read logs before calling it infra |
| -9 / 137 | SIGKILL; often OOM, sometimes a crash consequence | read logs first |

### Infra-only shortcut

If every real-failure root is exit 128, or reruns produce a random-different-error each time, and no log gives a real traceback: treat as infra. Cancel active runs, post `/rerun-failed-ci`, done — it'll auto-upgrade to full rerun if sgl-kernel wheels need rebuilding.

## 3. Rerun — prefer slash commands over `gh run rerun`

### Pick the right slash command

Only the first line of the comment is parsed. Handler: `scripts/ci/utils/slash_command_handler.py`.

| Command | What it actually does |
|---|---|
| `/rerun-failed-ci` | For every **completed** run on the head SHA with conclusion `failure` or `skipped`: if the PR touches `sgl-kernel/` **and** not all `Build Wheel*` check-runs are green (CUDA + ARM), call `run.rerun()` (full rerun); otherwise `run.rerun_failed_jobs()` (failed jobs + their dependents). Iterates across **every** workflow on the head SHA (pr-test, pr-test-extra, pr-test-sgl-kernel, jit-kernel, multimodal-gen…) so sibling-workflow failures are covered. Fast-fail-victim jobs conclude `failure` and are covered; cancelled workflow runs are ignored by the handler. |
| `/tag-and-rerun-ci` | `/tag-run-ci-label` + sleep 5s + `/rerun-failed-ci`. Use when the PR is missing `run-ci` and also has reds to retry in one shot. |
| `/tag-run-ci-label` | Adds the `run-ci` label. Nothing else. |
| `/rerun-test <file>...` | `workflow_dispatch` on the separate **`Rerun Test`** workflow (`rerun-test.yml`). Resolves each file by looking up its `register_cuda_ci(stage=, runner_config=)` (or `register_cpu_ci(...)`) decorator and dispatches one run per test on the appropriate runner. Investigation tool — runs the listed tests independently of pr-test.yml. Does NOT update PR checks; the original red stays red. Fork PRs are allowed only when the commenter has write/admin permission; untrusted fork authors are blocked. |
| `/rerun-group <group>...` | Expands each group name to all `test_*.py` files under `test/registered/<group>/` and reuses `/rerun-test` dispatch. Use when you know which module is flaky (e.g. `hicache`, `core`, `dsv4`) but don't want to enumerate files yourself. Same fork permission rule as `/rerun-test`: write/admin commenters can run it on fork PRs; untrusted fork authors cannot. |
| `/rerun-stage` | **DEPRECATED** (PR #25322). The handler now replies with a `-1` reaction and a comment pointing to alternatives, and does nothing. Don't post it. |

**Label-level controls** (use `gh pr edit <PR> --repo sgl-project/sglang --add-label <name>` to apply):

- `run-ci` — required by `pr-gate.yml`. Without it, base CI never fires.
- `run-ci-extra` — opts the PR into `pr-test-extra.yml` (nightly-class extras: `extra-a-test-*`, `extra-b-test-*`, deepep, etc.). Requires `run-ci` to also be present.
- `bypass-fastfail` — skips the jobs-failed check in `check-pr-test-health` and short-circuits the wait-for-base-* jobs to success. Use when diagnosing intermittent failures and you want every stage to run to completion so you can see all the failures, not just the first one. **Lint cascade is NOT bypassed.**
- `bypass-maintenance` — bypasses the rebase-required gate and the full-maintenance-pause gate (maintenance issue #21065). Use this only when a CI-fix PR genuinely needs to land during maintenance. `/rerun-test` also honors it; the pre-dispatch gate inside the slash handler refuses without the label.

### Decision tree (goal: turn the PR green)

1. No `run-ci` label (Case A) — `/tag-and-rerun-ci`.
2. Lint red (Case B) — fix lint first; reruns are wasted until lint is green.
3. Everything else — `/rerun-failed-ci`. It already handles `sgl-kernel/` correctly (full rerun if any `Build Wheel*` check-run isn't `success`, partial otherwise) and covers sibling workflows on the same SHA, so you don't need to pre-upgrade to `/tag-and-rerun-ci` just because the PR touches kernel code.

`/rerun-test` and `/rerun-group` are **off the tree** — they're investigation tools that dispatch the separate `Rerun Test` workflow and don't update the PR's pr-test checks. The original red stays red. Run them in parallel with a tree action when you want independent flake evidence or want to confirm a single suspected file before paying for a full rerun.

Stage-level retry (the deprecated `/rerun-stage`) is no longer an option — the maintainer position is that stage granularity is too coarse. If you really want only one stage's tests, use `/rerun-test` with the file list, or `/rerun-group <group>` if the stage maps cleanly to a `test/registered/` directory.

### Issue it

If the run is already terminal, post the slash command directly:

```bash
gh pr comment <PR> --repo sgl-project/sglang --body "/rerun-failed-ci"
```

If the run is still active, `/rerun-failed-ci` cannot pick it up yet because the handler only scans completed `failure` / `skipped` workflow runs. Do **not** cancel an active run and then post `/rerun-failed-ci`: cancelled workflow runs are ignored by the handler, so the comment can no-op.

When an active run is doomed, choose one of these:

- Normal path: leave the run alone, poll to terminal, then post `/rerun-failed-ci`.
- Urgent infra-budget path: cancel the active run, wait for the cancellation to land, then rerun that same workflow run explicitly with `gh run rerun <RUN_ID>`. This is the exception to the slash-command preference; it skips the handler's multi-workflow / kernel-wheel logic, so use it only when you intentionally want to replace that exact active run.

### Flaky root + in-flight cascade: don't use `/rerun-failed-ci` until terminal

If the real failure is a known-flaky test (transient, matches a known flake pattern, or an infra wobble) **and** the run still has pending jobs that will fast-fail from the cascade, waiting out the queue gives little signal, but the slash-command handler still won't see the run until it is terminal. Either wait/poll until terminal and then post `/rerun-failed-ci`, or use the urgent path above:

```bash
gh run cancel <RUN_ID> --repo sgl-project/sglang
gh run watch <RUN_ID> --repo sgl-project/sglang
gh run rerun <RUN_ID> --repo sgl-project/sglang
```

Do not pair `gh run cancel` with `/rerun-failed-ci`; cancelled workflow runs do not match the handler's `failure` / `skipped` filter.

When to pick something other than this:

- PR missing `run-ci` label → `/tag-and-rerun-ci` (adds label + runs rerun-failed-ci).
- Failure is **not** flaky — cancel + rerun just reproduces it and burns budget.

If you want to **confirm** the flake reproduces before burning a full rerun, fire `/rerun-test <test-path>` in parallel with the wait or urgent rerun — it dispatches a separate `Rerun Test` workflow and won't slow the main pipeline. Remember it doesn't green the PR on its own.

### Confirm it fired

The slash-command handler runs on `issue_comment` events and is **invisible** on the PR's Checks tab. Verify:

```bash
gh api "repos/sgl-project/sglang/actions/runs?event=issue_comment&per_page=3" \
  --jq '.workflow_runs[] | {name, status, created_at, html_url}'
```

### Then immediately open a polling cron — do not ask

After confirming the rerun fired, **open a `CronCreate` poll right away** at the default 30 min cadence to babysit to terminal. Do **not** ask "want me to open a cron?" — the user already authorized this when they invoked the skill. Same applies any time you've left CI in a pending state and the user implicitly wants completion.

```
CronCreate(schedule: "*/30 * * * *", prompt: "Check CI for PR <N> on sgl-project/sglang. Report job-level deltas since last tick. Then apply the adaptive cadence rules in the sglang-ci-monitor skill (§4) — if the new cadence differs from the current one, CronDelete this trigger and CronCreate a replacement at the new cadence. When every rollup check is terminal or the PR is closed, call CronDelete with this trigger's id and stop.")
```

The prompt offloads cadence decisions to the rules in §4 so the cron body stays short.

### Why prefer slash commands over `gh run rerun`

`gh run rerun` works, but skips three pieces of logic in `slash_command_handler.py` that you want:

- **Kernel wheel freshness**: the handler inspects `Build Wheel*` check-runs (CUDA + ARM) and upgrades to full `run.rerun()` if any didn't pass. `gh run rerun --failed` always does partial — you can silently test against a stale wheel.
- **Multi-workflow sweep**: `/rerun-failed-ci` iterates **every** workflow run on the head SHA (pr-test, pr-test-extra, pr-test-sgl-kernel, jit-kernel, multimodal-gen…) so a failed sibling workflow gets retried in the same command. `gh run rerun <ID>` retries one run.
- **Label / maintenance handling**: `/tag-and-rerun-ci` applies `run-ci` before rerunning, and `/rerun-test` pre-checks the maintenance gate. `gh run rerun` won't touch labels or pre-check maintenance, so reruns can fast-deny without warning.

pr-gate's 120-min rate limit bypasses for maintainers, so it's not a concern here.

## 4. Waiting for long things

Prompt cache has a 5-minute TTL. Polling every few minutes burns cache misses repeatedly. Pick the right tool for the timescale:

- **Local process / server readiness** (seconds–minutes): `Monitor` tool on the log, or a short `until` loop — the harness supports these but blocks long leading sleeps.
  ```bash
  until curl -sf http://localhost:30000/health; do sleep 2; done
  ```
- **CI of ~30 min or more**: `CronCreate` every **30 min by default**, with a prompt that (a) re-pulls the rollup, (b) reports only changed/terminal states, (c) applies the adaptive cadence rules below to pick the next interval, and (d) calls `CronDelete` on its own trigger id when all checks are terminal or the PR is closed.
  ```
  CronCreate(schedule: "*/30 * * * *", prompt: "Check CI for PR N on sgl-project/sglang. Report job-level deltas since last tick. Then apply the adaptive cadence rules in the sglang-ci-monitor skill (§4) — if the new cadence differs from the current one, CronDelete this trigger and CronCreate a replacement at the new cadence. When every rollup check is terminal or the PR is closed, call CronDelete with this trigger's id and stop.")
  ```
- **Never**: backgrounded shell polling (`while true; do ...; sleep 60; done`), `sleep 300 && tail`, or any pattern that keeps a shell alive just to wait. The harness rejects long leading sleeps, and even when it doesn't, the cost/cache math is wrong.

### Adaptive cadence — extend when status warrants it

**Default**: 30 min. Each cron tick, after reporting deltas, decide the next interval and self-adjust by `CronDelete` + `CronCreate` if it changes. There is no `CronUpdate`; replace the trigger when you need a different schedule.

Rules (apply the **first** match, then stop):

| Signal observed this tick | Next cadence |
|---|---|
| Every non-AMD check is terminal **or** PR closed | `CronDelete` this trigger and stop. |
| ≤3 non-terminal jobs left **and** each has been running ≤ its typical wall time | **15 min** — catch terminal-state quickly so the user gets a final report fast. |
| Steady progress: ≥1 job flipped to terminal since last tick, queue still draining | keep **30 min**. |
| No state changes for 2 consecutive ticks, but jobs are still actively running (not stuck queued) | extend to **45 min**. |
| A long-tail base-c job (or `pr-test-extra` stage) is the only blocker, still in its expected runtime window | **45 min**. |
| Run looks stuck: same job queued >40 min without starting, **or** the same in-progress job has run >2× its typical wall time | extend to **60 min** — and surface the stuck job by name in the next report so the user sees it. |
| `pr-gate` denied / no `run-ci` label / nothing actually running | **stop the cron** and post `/tag-and-rerun-ci` instead — polling a non-running PR is wasted ticks. |

Only **shorten below 30 min** in the "≤3 jobs left" case. Don't oscillate — if you just shortened to 15 min and the next tick is still pending, hold at 15 min rather than bouncing back up.

Schedule replacement pattern (when the rule says the cadence changed):

```
CronDelete(<this trigger id>)
CronCreate(schedule: "*/45 * * * *", prompt: "<same prompt as before, with PR N substituted>")
```

Keep the prompt body identical across replacements so subsequent ticks keep applying these rules.

## What to not do

- Don't default to `gh run rerun` — it skips the handler's kernel-wheel detection, multi-workflow sweep, label handling, and maintenance gate.
- Don't post `/rerun-stage` — it's deprecated (PR #25322). The handler replies with `-1` and a comment but does nothing useful. Use `/rerun-failed-ci`, `/rerun-test <files>`, or `/rerun-group <group>` instead.
- Don't use the old `stage-{a,b,c}-test-*` names when discussing NVIDIA CI — they're now `base-{a,b,c}-test-*`. (AMD CI is the exception: AMD jobs still use the `stage-*` prefix.)
- Don't confuse `pr-test.yml` (base CI) with `pr-test-extra.yml` (extra CI). They are separate workflows with separate run ids, separate label gates, and separate stages (`base-a/b/c` vs `extra-a/b`).
- Never background-shell-poll for CI; use `CronCreate`.
- Never call exit 255 "runner crash" without reading the log.
- Never investigate fast-fail victims individually — find the root. (Exception: PR carries `bypass-fastfail`, in which case there are no victims — every FAILURE is real.)
- Never report raw FAILURE counts from `statusCheckRollup` without first splitting by duration. A cascade of 8 victims + 1 root is not "9 failures"; it's 1.
- Never trust the PR page's Checks list alone — it misses base-c jobs and issue_comment workflows. The PR States block at the bottom of the PR body and `statusCheckRollup` are the authoritative views.
- Never stop mid-flow to ask "should I?" — see Execution policy.
- Never ask "want me to open a cron?" after a rerun — open it directly. The user already opted in by invoking the skill.
