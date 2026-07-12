#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/swe-bench-pro}"
REPO="$ROOT/SWE-bench_Pro-os"

mkdir -p "$ROOT"
if [ ! -d "$REPO/.git" ]; then
  git clone --recurse-submodules https://github.com/scaleapi/SWE-bench_Pro-os.git "$REPO"
fi

git -C "$REPO" submodule update --init SWE-agent mini-swe-agent
git -C "$REPO/mini-swe-agent" fetch origin main
git -C "$REPO/mini-swe-agent" checkout --detach origin/main

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

cd "$REPO"
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install -e mini-swe-agent

test -f mini-swe-agent/src/minisweagent/config/swebp.yaml
mini-extra run-batch --help >/dev/null

{
  git rev-parse HEAD
  git -C mini-swe-agent rev-parse HEAD
  git -C SWE-agent rev-parse HEAD
} > RUNNER_REVISIONS.txt

printf 'Ready: %s\n' "$REPO"
printf 'Revisions: %s\n' "$REPO/RUNNER_REVISIONS.txt"
