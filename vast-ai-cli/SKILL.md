---
name: vast-ai-cli
description: >-
  Operate Vast.ai GPU instances via the `vastai` CLI — search offers, set
  API/SSH keys, create/start/stop/destroy instances, poll status, SSH in, copy
  data, and clean up to stop storage charges. Use whenever the user mentions
  Vast.ai, vastai, GPU rental, spinning up a remote training/inference box, or
  troubleshooting vastai commands — including casual phrasings like "rent a
  4090", "spin up a Vast instance", "租个 GPU", "开个 vast 实例", "destroy that
  instance". Read-only commands (`show`, `search`, help) are safe; anything
  billable or destructive needs explicit user approval first.
---

# Vast.ai CLI

Use this skill to operate Vast.ai GPU instances through the `vastai` CLI. Prefer
official CLI docs and local `vastai ... --help` for exact flags because the
platform and command set can change.

## Operating Rules

- Treat read-only commands as safe: `show`, `search`, `ssh-url`, command help.
- Do not create, start, stop, destroy, prepay, change bids, transfer credits, or
  delete resources unless the user explicitly asked for that action or approved
  the exact operation.
- Before any command that can start billing, state the offer/instance ID, max
  hourly cost or bid if known, disk size, Docker image/template, and cleanup
  plan. If those are missing, run a read-only search and ask before renting.
- Never print, paste, commit, or log API keys. Prefer `vastai set api-key` or a
  short-lived environment variable over embedding keys in scripts.
- If you create an instance, track the instance ID, label, status, and the exact
  cleanup command in your notes and final answer.
- Use `--raw` for commands that support it when automation needs JSON. Use
  `--explain` or `--curl` when you need to understand the REST API mapping.

## Official Docs To Check

- Docs index: `https://docs.vast.ai/llms.txt`
- CLI hello world: `https://docs.vast.ai/cli/hello-world`
- CLI quickstart/reference entry: `https://docs.vast.ai/cli/get-started`
- CLI commands reference: `https://docs.vast.ai/cli/commands`
- Search offers API reference: `https://docs.vast.ai/api-reference/search/search-offers`
- Create instance API reference: `https://docs.vast.ai/api-reference/instances/create-instance`
- SSH guide: `https://docs.vast.ai/guides/instances/connect/ssh`
- Data movement guide: `https://docs.vast.ai/guides/instances/storage/data-movement`
- Permissions: `https://docs.vast.ai/api-reference/permissions-and-authorization`

## Setup

Install or update the CLI:

```bash
python -m pip install -U vastai
vastai --help
vastai --version
```

Authenticate:

```bash
vastai set api-key "$VAST_API_KEY"
vastai show user --raw
```

For CI or shared automation, create a scoped key instead of using a full-access
console key. Draft a permissions JSON from the permissions docs, then inspect
the installed CLI before creating the key because older command help uses
`--permissions` while newer docs may show `--permission_file`:

```bash
vastai create api-key --help
vastai create api-key --name NAME --permission_file permissions.json --raw
```

The newly created key may only be shown once. Store it securely and do not echo
the full value back to the user unless they explicitly asked to see their own
secret.

Register SSH keys before creating instances; account keys are applied at
container creation time:

```bash
vastai create ssh-key ~/.ssh/id_ed25519.pub
```

If no key exists and the user wants the CLI to generate one:

```bash
vastai create ssh-key
```

## Search Offers

Start with a narrow read-only search, then relax filters if no offers appear.
Common filters include `gpu_name`, `num_gpus`, `gpu_ram`, `reliability`,
`verified`, `rentable`, `dph`, `geolocation`, `direct_port_count`,
`compute_cap`, `cuda_max_good`, `inet_down`, `inet_up`, and `disk_space`.

Example:

```bash
vastai search offers 'gpu_name=RTX_4090 num_gpus=1 verified=true rentable=true direct_port_count>=1 dph<=1.00' -o 'dlperf_usd-' --raw
```

If the installed CLI rejects `-o`, inspect:

```bash
vastai search offers --help
```

Selection checklist:

- Prefer `verified=true` and high reliability for unattended work.
- Require `direct_port_count>=1` when direct SSH, HTTP serving, or low-latency
  file transfer matters.
- Use `dph` as the CLI query field for price. Check output fields such as
  `dph_total`, storage cost, bandwidth cost, disk space, CUDA/driver
  compatibility, country/region, and GPU RAM.
- Offers are dynamic; if creation fails because the offer disappeared, search
  again and try another approved offer.

## Create Instances

Use an offer ID returned by `search offers`. Use `--image` for direct image
launches, or `--template_hash` for a saved template.

```bash
vastai create instance "$OFFER_ID" \
  --image pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime \
  --disk 50 \
  --ssh --direct \
  --label "$LABEL" \
  --onstart-cmd 'env >> /etc/environment; nvidia-smi' \
  --raw
```

For exposed services, pass Docker-style env and port args through `--env`:

```bash
vastai create instance "$OFFER_ID" \
  --image vllm/vllm-openai:latest \
  --disk 80 --ssh --direct \
  --env '-e MODEL_ID=deepseek-ai/DeepSeek-R1-Distill-Llama-8B -p 8000:8000' \
  --onstart-cmd 'env >> /etc/environment; vllm serve "$MODEL_ID" --port 8000' \
  --raw
```

Template examples:

```bash
vastai create instance "$OFFER_ID" --template_hash "$TEMPLATE_HASH" --raw
vastai create instance "$OFFER_ID" --template_hash "$TEMPLATE_HASH" --disk 100 --env "-e HF_TOKEN=$HF_TOKEN" --raw
```

Notes:

- `--onstart-cmd` is limited; use an onstart file, template, or a compressed
  bootstrap pattern for large scripts.
- CLI `--env` uses Docker-style flags. The raw API uses JSON object syntax; do
  not mix the two formats.
- Pass secrets through environment variables or Vast encrypted env vars. Do not
  paste literal tokens into commands that will be logged.
- Include `env >> /etc/environment` in onstart when variables must be visible
  in later SSH sessions.
- Creation output contains `new_contract`; treat that as the instance ID.

## Poll And Inspect

Poll with a timeout. Do not loop forever because disk charges begin at creation.

```bash
INSTANCE_ID=12345678
deadline=$((SECONDS + 900))
while (( SECONDS < deadline )); do
  json=$(vastai show instance "$INSTANCE_ID" --raw)
  status=$(jq -r '.actual_status // .status // .instances.actual_status // .instances.status // empty' <<<"$json")
  echo "status=$status"
  case "$status" in
    running) break ;;
    exited|unknown|offline) echo "$json"; exit 1 ;;
  esac
  sleep 15
done
```

Useful inspection commands:

```bash
vastai show instances --raw
vastai show instance "$INSTANCE_ID" --raw
vastai logs "$INSTANCE_ID" --raw
vastai execute "$INSTANCE_ID" 'nvidia-smi'
```

`vastai execute` is constrained and best for short remote commands. Use SSH for
interactive or long-running work.

## Connect

Get SSH details:

```bash
vastai ssh-url "$INSTANCE_ID"
```

Then connect with the returned host and port:

```bash
ssh root@SSH_HOST -p SSH_PORT
```

For services on the instance, use SSH local forwarding:

```bash
ssh root@SSH_HOST -p SSH_PORT -L 8000:localhost:8000
```

Troubleshooting:

- Vast.ai uses key-based SSH. If access fails, verify the key was registered
  before instance creation, private-key permissions are `chmod 600`, and the
  host/port came from the current instance.
- Direct SSH is preferred for speed, but requires direct ports. Proxy SSH works
  more broadly and can be slower.
- Vast.ai sessions may start in tmux. Use normal tmux controls or create
  `~/.no_auto_tmux` inside the instance to disable auto-tmux.

## Copy Data

Use `vastai copy` for local-instance, instance-instance, and some cloud paths:

```bash
# Upload
vastai copy local:./data/ "$INSTANCE_ID":/workspace/data/

# Download
vastai copy "$INSTANCE_ID":/workspace/results/ local:./results/

# Structured container syntax
vastai copy local:data/test C."$INSTANCE_ID":/data/test
vastai copy C."$INSTANCE_ID":/data/test local:data/test

# Instance to instance
vastai copy "$SRC_INSTANCE":/workspace/ "$DST_INSTANCE":/workspace/

# Cloud storage after a connection is configured
vastai copy s3."$CONNECTION_ID":/bucket/data/ "$INSTANCE_ID":/workspace/
vastai cloud copy --help
```

Avoid copying into `/root` or `/` on an instance; it can break SSH/copy
permissions. For VM instances, `vastai copy SRC_ID DST_ID` performs a full disk
migration and replaces the destination disk.

For small one-off transfers over SSH:

```bash
scp -P SSH_PORT file.txt root@SSH_HOST:/workspace/
```

Use uppercase `-P` for `scp`; `ssh` uses lowercase `-p`.

## Manage And Clean Up

```bash
vastai stop instance "$INSTANCE_ID" --raw
vastai start instance "$INSTANCE_ID" --raw
vastai reboot instance "$INSTANCE_ID" --raw
vastai destroy instance "$INSTANCE_ID" --raw
```

Billing semantics:

- `stop instance` stops compute billing but keeps storage and data.
- `start instance` resumes a stopped instance, subject to host resource
  availability.
- `destroy instance` is irreversible and deletes data; it is the normal final
  cleanup to stop ongoing storage charges.

Before destroying, verify results were copied out if the user needs them:

```bash
vastai show instance "$INSTANCE_ID" --raw
vastai copy "$INSTANCE_ID":/workspace/results/ local:./results/
vastai destroy instance "$INSTANCE_ID" --raw
```

## Common Failure Modes

- Authentication error: rerun `vastai show user --raw`; reset the key with
  `vastai set api-key` or pass `--api-key` for a single command.
- No offers found: relax filters in order: region, exact GPU model, direct
  ports, price cap, reliability threshold.
- Offer gone during creation: search fresh and use another approved offer.
- Stuck `loading`: inspect logs and Docker image size; keep a timeout.
- `exited`, `unknown`, or `offline`: do not wait forever. Collect diagnostics,
  stop/destroy as appropriate, and try a different offer if the user approved.
- Env vars missing in SSH: add `env >> /etc/environment` to onstart.
- CLI flag mismatch: run `vastai COMMAND --help`, then update the command. The
  docs and installed package can drift.
