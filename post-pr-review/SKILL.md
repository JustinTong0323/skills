---
name: post-pr-review
description: >-
  Post a completed code review to a GitHub PR as file-anchored inline comments. Use whenever the user says "post the review", "submit the review", "把评审发到PR", "把review发上去", "把 comment post 上去", "提交 PR 评审", "提 PR review", or confirms publishing after a `/review-pr` / `/pr-review-toolkit:review-pr` analysis — even when they don't explicitly name the tool. Default to this skill instead of `gh pr review --comment` or `gh pr comment`; those post unanchored comments and have been rejected by the user ("你没有follow claude.md的要求吗？需要post到 PR固定的位置"). Sharp edges: abbreviated commit SHA gets a misleading "Variable $commitOID invalid value" error; local HEAD drifts after force-push and triggers "commit_id is not part of the pull request"; `json.load(sys.stdin)` on `gh api` output chokes on error-path suffixes.
---

# post-pr-review

Post a finalized code review to a GitHub PR as inline file-anchored comments, in one atomic POST.

Default path is `gh api .../pulls/<N>/reviews --method POST --input <json>`. Do **not** use `gh pr review --comment` or `gh pr comment` — those produce unanchored/issue-level comments and the user treats them as a failure.

## Execution policy — act, don't ask

When this skill triggers, the user has already decided to publish. Don't stop to re-confirm the workflow. Run the full flow in one pass with one-sentence narration.

Pre-approved on the **user's own PR**:
- `gh api` reads (`pulls/<N>`, `pulls/<N>/comments`, `pulls/<N>/reviews`).
- Writing the payload JSON under `/tmp/`.
- POSTing `/pulls/<N>/reviews`.
- Retracting a review with `DELETE /pulls/<N>/reviews/<id>` if the user says the posted one is wrong.

Stop and ask first when:
- PR author is not the user (`gh pr view <N> --repo <owner>/<repo> --json author --jq .author.login` vs `gh api user --jq .login`). Posting to a stranger's PR without permission is intrusive.
- `event` would be `APPROVE` or `REQUEST_CHANGES` without explicit user instruction. Default to `COMMENT`.

## Flow

### 1. Collect anchors

For each finding, you need `path`, `line`, and `side` (`RIGHT` = new code, `LEFT` = removed/old). If line numbers aren't already in the review notes:
```bash
grep -n "<needle>" <file>
```

Get the PR head SHA **from the PR API**, full 40 chars:
```bash
gh api repos/<owner>/<repo>/pulls/<N> --jq '.head.sha'
```

Never use `git rev-parse HEAD` — the worktree may be behind a force-push. Never abbreviate the SHA — the reviews endpoint will reject it with a misleading "Variable $commitOID of type GitObjectID was provided invalid value".

### 2. Build the payload at `/tmp/pr<N>-review.json`

```json
{
  "commit_id": "<full 40-char sha from step 1>",
  "event": "COMMENT",
  "body": "<top-level summary — terse, human-voice>",
  "comments": [
    {"path": "python/sglang/srt/foo.py", "line": 66, "side": "RIGHT", "body": "..."},
    {"path": "test/bar.py", "line": 15, "side": "RIGHT", "body": "..."}
  ]
}
```

Fields:
- `event`: `COMMENT` publishes immediately without gating. `APPROVE` / `REQUEST_CHANGES` gate the PR — only use if the user explicitly asked. Omit or `PENDING` leaves a draft that's invisible to the PR author until submitted later (rarely what's wanted).
- `comments[]`: one entry per finding. Multi-file is just more entries — one POST covers the whole review.
- Multi-line comments: add `"start_line": <n>, "start_side": "RIGHT"` alongside `line` (line is the bottom of the range).
- `path` is repo-relative and must match the diff exactly (including leading directory like `python/sglang/...`).

#### Style — the body text

Review bodies must not read like LLM output. See memory `feedback_pr_review_comment_style.md` for the full rule; applied here:
- Lead with the concrete issue. No "Great work overall, but…".
- Cite file:line once, don't restate the diff.
- Imperative voice: "Use X instead" not "Consider using X".
- No preamble, no sign-off, no "happy to help".
- One sentence per point unless truly needed.

The top-level `body` follows the same rules. Often a single sentence summarizing the theme is enough; if there's nothing thematic to say, omit or use an empty string.

### 3. Submit in one shot

```bash
gh api repos/<owner>/<repo>/pulls/<N>/reviews --method POST \
    --input /tmp/pr<N>-review.json 2>&1 | head -50
```

Success: response has `"state": "COMMENTED"` and `"html_url": ".../pull/<N>#pullrequestreview-<id>"`. Paste that URL back to the user as confirmation — that's proof it landed as a proper review, not a stray issue comment.

Use `2>&1 | head -50` (not `--jq`) so a validation error stays visible. `gh api` on an error appends human-readable text after the JSON; piping to `python -c "json.load(sys.stdin)"` breaks with `JSONDecodeError: Extra data`.

## Failure modes and fixes

### `HTTP 422 "Variable $commitOID of type GitObjectID was provided invalid value"`
`commit_id` is abbreviated or malformed. Refetch:
```bash
gh api repos/<owner>/<repo>/pulls/<N> --jq '.head.sha'
```
Regenerate the payload with the full 40-char SHA and re-POST.

### `HTTP 422 "commit_id is not part of the pull request"`
Local HEAD is stale (the PR was force-pushed since you checked out). Same fix: refetch `head.sha` from the PR API. Never substitute `git rev-parse HEAD`.

### Output looks mangled when piping to python
`gh api` prints the JSON body, then appends `gh: Validation Failed (HTTP 422)` on error. That tail breaks `json.load(sys.stdin)`. Two safe options:
- `--jq '.some_field'` — gh handles errors cleanly.
- `2>&1 | head -50` — lets you eyeball both success JSON and error text.

### Accidentally used `gh pr comment`
That posts to the PR issue-comment stream, which is not anchored to lines and is the exact failure the user has pushed back on ("你没有follow claude.md的要求"). Retract the issue comment (`gh api repos/<owner>/<repo>/issues/comments/<id> -X DELETE`) and redo via `/pulls/<N>/reviews`.

## Retract a bad review and redo

If the wrong review landed (wrong target, wrong content, APPROVE instead of COMMENT):
```bash
# Find it
gh api repos/<owner>/<repo>/pulls/<N>/reviews --jq '.[] | {id, state, body}'
# Delete it
gh api repos/<owner>/<repo>/pulls/<N>/reviews/<REVIEW_ID> -X DELETE
```
Then rebuild the payload and POST again.

## Fallback — per-comment mode

Only use this if the batch POST hit a path-specific error (e.g., one line is outside the diff and you want to skip it) or for a quick one-off follow-up:
```bash
gh api -X POST repos/<owner>/<repo>/pulls/<N>/comments \
    -f commit_id=<full sha> \
    -f path="python/.../foo.py" \
    -F line=179 \
    -f side=RIGHT \
    -f body='...' \
    --jq '.html_url'
```
Note `-F` (numeric) for `line`; `-f` (string) for everything else. Each comment is a separate POST — no batching.

## Read existing review comments (for "resolve comments" flows)

```bash
gh api repos/<owner>/<repo>/pulls/<N>/comments \
    --jq '.[] | {path, line, body: .body[:400], user: .user.login, created_at}'
```
`/pulls/<N>/comments` is inline review comments. `/issues/<N>/comments` is the separate top-level PR conversation stream — don't confuse them.

## What this skill does NOT cover

- Running the review itself — that's `/review-pr` / `/pr-review-toolkit:review-pr`. This skill only publishes a finalized review.
- Creating a new PR — `commit-commands:commit-push-pr`.
- Replying in a thread on an existing comment — use `POST /pulls/<N>/comments/<id>/replies` (future extension).
