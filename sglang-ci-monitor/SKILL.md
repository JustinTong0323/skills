---
name: sglang-ci-monitor
description: >-
  Check, diagnose, rerun, and wait on CI for a sglang PR. Use whenever the user asks about CI on a sglang PR, wants to monitor a run, diagnose a CI failure, rerun failed jobs, or wait for CI to finish — even when they phrase it as "is the PR green?", "what's happening with the tests?", "something failed, look at it", or "babysit this PR for me". Assumes the user is a sglang CI maintainer, so all slash commands are available. Sharp edges: fast-fail cascades that hide the real root, `/rerun-failed-ci` vs `/tag-and-rerun-ci` vs `/rerun-stage` doing subtly different things, issue_comment-based reruns invisible on the PR page, exit-255 ambiguity — this skill is the default playbook.
---

# sglang CI monitor

The correct way to work with `sgl-project/sglang` CI. Naive moves (`gh run rerun`, `sleep && tail`, reading the PR page) look right and waste hours. Follow the patterns here.

All `gh` commands pass `--repo sgl-project/sglang` explicitly because the active worktree is usually a fork.

## Execution policy — act, don't ask

When this skill triggers, the user's ask ("monitor / diagnose / rerun / babysit") is the approval. Run the whole flow in one pass. Do **not** stop mid-flow to re-confirm.

Pre-approved (just do them, with one sentence of narration) — **on any sgl-project/sglang PR, regardless of author**. The user is a CI maintainer with permissions across the whole repo:

- All read-only `gh` queries (`gh pr view`, `gh api .../runs|jobs|annotations|logs`).
- Posting slash commands: `/rerun-failed-ci`, `/tag-and-rerun-ci`, `/rerun-stage`, `/rerun-test`, `/tag-run-ci-label`.
- `gh run cancel` as the precursor to a rerun.
- `CronCreate` for polling.

Stop and ask only when:

- The action is off the pre-approved list (force-push, close, delete branch, edit labels outside the slash flow).
- The next step is a **code change** in response to a real failure — diagnosis is autonomous, fixing is not.

Do **not** pause to ask "PR isn't yours, ok to proceed?" — the user has explicitly waived that check.

## 1. Start the flow

### Scope: NVIDIA only

Only monitor NVIDIA CI. **Filter out AMD jobs entirely** — anything matching `*-amd*` (e.g. `stage-{a,b,c}-test-*-amd*`, `sgl-kernel-unit-test*-amd`, `PR Test (AMD)` workflow) is out of scope for this skill.

- When pulling the rollup, exclude AMD jobs before splitting failures into root vs. cascade.
- Don't report AMD failures, don't diagnose them, don't rerun them.
- Cron polling considers CI "terminal" when every **non-AMD** check is terminal — ignore AMD pending state.
- Never use `/rerun-stage` with an AMD stage name.

### Resolve the PR

If the user didn't name a PR:

```bash
gh pr view --json number,author,headRefName --head "$(git branch --show-current)"
```

If no PR is associated, ask once for a number or URL. Otherwise proceed. Do not gate on PR ownership — author check is intentionally skipped (see Execution policy).

### Pull job-level status

The PR page's Checks list and `gh pr checks` both miss stage-c jobs. Always use `statusCheckRollup`:

```bash
gh pr view <PR> --repo sgl-project/sglang --json statusCheckRollup,labels \
  --jq '{labels: .labels | map(.name),
         checks: .statusCheckRollup | group_by(.conclusion)
                 | map({conclusion: .[0].conclusion, count: length})}'
```

### Always separate real failures from fast-fail victims

sgl CI uses aggressive fast-fail (`.github/actions/check-stage-health`): when any earlier job fails, the stage-health check inside every downstream job calls `core.setFailed('Fast-fail: skipping — root cause job(s): <names>')`. A raw count of FAILURE in the rollup is misleading — "8 FAILURE" often means "1 root + 7 cascade victims".

**The authoritative way: read the annotation** on any fast-fail job. It names the real roots directly:

```bash
gh api repos/sgl-project/sglang/check-runs/<JOB_ID>/annotations --jq '.[].message'
# → "Fast-fail: skipping — root cause job(s): unit-test-backend-4-gpu, lint check failed"
# or → "Fast-fail: lint check failed"   (if lint.yml itself is red)
```

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

`<RUN_ID>` is the PR Test run — find it from the rollup's `detailsUrl` on any non-gate check, or with `gh run list --workflow=pr-test --branch <head> --limit 1 --repo sgl-project/sglang`.

### Report style

Lead with the conclusion, with cascades collapsed. Counts first, prose second.

Good:

> 1 real failure (`unit-test-cpu-2`, exit 255, 4m18s), 7 fast-fail victims, 12 SUCCESS. Diagnosing the real one now.

> 2 labels, 8 pr-gate denials (all fast — no `run-ci` label), 50 SKIPPED, 12 SUCCESS. CI never fired. Posting `/tag-run-ci-label`.

Bad:

> I ran `gh pr view` to check the status. Looking at the output, it seems that there are 8 failures. Let me investigate further…

Don't narrate queries. Don't report raw FAILURE counts without first collapsing cascades. Don't ask what to do next when the flow already dictates it.

## 2. Diagnose failures

§1 gives you the roots (from annotations, or the duration fallback). Match your situation to a case below. Most sessions are Case A, C, or D; the rest are rarer.

### Case A: nothing is running (CI never fired)

Symptom (from `pr-gate.yml`): pr-gate exits with `Missing required label 'run-ci'` and every downstream job is SKIPPED. The rollup shows 0 `run-ci` label, pr-gate FAILUREs, then a sea of SKIPPED.

Action: `/tag-and-rerun-ci` (adds label and reruns in one shot). No further diagnosis — nothing actually ran yet.

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

The root lives in a sibling workflow (e.g. `Build Wheel` in `pr-test-sgl-kernel.yml`). `rerun_failed_jobs` is scoped to a single run, so a failed wheel build won't get retried by `/rerun-failed-ci` unless you look at the right run:

```bash
gh run list --branch <head> --limit 5 --repo sgl-project/sglang
```

Diagnose the parent run's failure directly.

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
| `/rerun-failed-ci` | For every run on the head SHA with conclusion `failure` or `skipped`: if the PR touches `sgl-kernel/` **and** not all `Build Wheel*` check-runs are green, call `run.rerun()` (full rerun); otherwise `run.rerun_failed_jobs()` (failed jobs + their dependents). Fast-fail victims have conclusion=failure so they're covered. |
| `/tag-and-rerun-ci` | `/tag-run-ci-label` + sleep 5s + `/rerun-failed-ci`. Use when the PR is missing `run-ci` and also has reds to retry in one shot. |
| `/tag-run-ci-label` | Adds the `run-ci` label. Nothing else. |
| `/rerun-stage <full-stage-name>` | Targeted retry: `workflow_dispatch` on `PR Test` with `target_stage=<name>`, running only that stage and skipping its dependencies. Stage must be in the whitelist (below). **Refused if PR touches `sgl-kernel/`** — `target_stage` mode skips the wheel build, so tests would run against the PyPI wheel. |
| `/rerun-test <test-path>` | `workflow_dispatch` on the separate **`Rerun Test`** workflow (`rerun-test.yml`). Investigation tool — runs the listed tests on a chosen runner, independently of PR Test. Does NOT update PR checks; the original red stays red. |

**`/rerun-stage` valid stage names** (whitelist in `handle_rerun_stage`, NVIDIA-only — see §1 Scope):

`stage-a-test-1-gpu-small`, `stage-a-test-cpu`, `stage-b-test-{1,2,4}-gpu-*`, `stage-c-test-{4,8}-gpu-*`, `multimodal-gen-*`.

Use the full stage name from the rollup, not `stage-a` / `stage-b`.

### Decision tree (goal: turn the PR green)

1. No `run-ci` label (Case A) — `/tag-and-rerun-ci`.
2. Lint red (Case B) — fix lint first; reruns are wasted until lint is green.
3. Want to retry one stage in isolation (skip its dependencies), no `sgl-kernel/` changes — `/rerun-stage <full-name>`.
4. Everything else — `/rerun-failed-ci`. It already handles `sgl-kernel/` correctly (full rerun if the wheel build didn't pass, partial otherwise), so you don't need to pre-upgrade to `/tag-and-rerun-ci` just because the PR touches kernel code.

`/rerun-test` is off the tree — it's an investigation tool, not a way to green the PR. Run it in parallel with a tree action when you want independent flake evidence.

### Issue it

If there's an active run on the PR **and** you're about to rerun because it's doomed (flaky root with pending cascade, or in-flight reds you want to replace), cancel first:

```bash
gh run cancel <RUN_ID> --repo sgl-project/sglang
```

Cancellation is **not** needed when the run is already terminal — `/rerun-failed-ci` picks up `failure`/`skipped` conclusions directly. Then post the comment:

```bash
gh pr comment <PR> --repo sgl-project/sglang --body "/tag-and-rerun-ci"
```

### Flaky root + in-flight cascade: cancel, don't wait

If the real failure is a known-flaky test (transient, matches a known flake pattern, or an infra wobble) **and** the run still has pending jobs that will fast-fail from the cascade, cancel the in-flight run immediately and rerun — waiting out the queue gives zero signal:

```bash
gh run cancel <RUN_ID> --repo sgl-project/sglang
gh pr comment <PR> --repo sgl-project/sglang --body "/rerun-failed-ci"
```

Cancelled jobs count as failure for `/rerun-failed-ci`, so this retries the root + all cascade victims + anything that was pending, while preserving already-green jobs. That's the right shape for "rerun everything that isn't already passing."

When to pick something other than this:

- PR missing `run-ci` label → `/tag-and-rerun-ci` (adds label + runs rerun-failed-ci).
- Failure is **not** flaky — cancel + rerun just reproduces it and burns budget.

If you want to **confirm** the flake reproduces before burning a full rerun, fire `/rerun-test <test-path>` in parallel with the cancel+rerun — it dispatches a separate `Rerun Test` workflow and won't slow the main pipeline. Remember it doesn't green the PR on its own.

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

`gh run rerun` works, but skips two pieces of logic in `slash_command_handler.py` that you want:

- **Kernel wheel freshness**: the handler inspects `Build Wheel*` check-runs and upgrades to full `run.rerun()` if any didn't pass. `gh run rerun --failed` always does partial — you can silently test against a stale wheel.
- **Label handling**: `/tag-and-rerun-ci` applies `run-ci` before rerunning. `gh run rerun` won't touch labels, so if the PR lacks `run-ci` all reruns fast-deny at pr-gate.

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
| A long-tail stage-c job is the only blocker (still in its expected runtime window) | **45 min**. |
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

- Don't default to `gh run rerun` — it skips the handler's kernel-wheel detection and label handling.
- Never background-shell-poll for CI; use `CronCreate`.
- Never call exit 255 "runner crash" without reading the log.
- Never investigate fast-fail victims individually — find the root.
- Never report raw FAILURE counts from `statusCheckRollup` without first splitting by duration. A cascade of 8 victims + 1 root is not "9 failures"; it's 1.
- Never trust the PR page's Checks list alone — it misses stage-c jobs and issue_comment workflows.
- Never stop mid-flow to ask "should I?" — see Execution policy.
- Never ask "want me to open a cron?" after a rerun — open it directly. The user already opted in by invoking the skill.
