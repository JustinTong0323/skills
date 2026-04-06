---
name: openclaw-interact
description: Interact with OpenClaw — send messages via chat channels and talk to the OpenClaw agent. Use this skill whenever someone wants to send a message through OpenClaw, ping the agent, ask the agent to do something, deliver a message to the user, or have a conversation with the OpenClaw agent. Also use it when the context implies communicating through OpenClaw channels, even if "openclaw" isn't mentioned explicitly (e.g., "send me a message", "tell the agent to check email", "ping the agent").
---

# openclaw-interact

Two ways to communicate through OpenClaw from the CLI:

1. **Direct message** — send a message straight to a chat channel, no agent involved
2. **Agent conversation** — send a prompt to the OpenClaw agent, who processes it and delivers a reply

## Prerequisites

- OpenClaw CLI installed and configured (`openclaw` command available)
- At least one channel configured (Telegram, Discord, WhatsApp, etc.)
- Gateway running (`openclaw gateway`)

## Configuration

Before using this skill, determine your setup by running:

```bash
# Check which channels are enabled
openclaw status

# Find your default chat target
openclaw directory self --channel telegram
```

Note the **channel name** and **target ID** for use in commands below. Replace `<channel>` and `<target>` in examples with your values.

## When to use which

- **Direct message**: you have a specific text you want to deliver as-is (notifications, reminders, status updates, forwarding info)
- **Agent conversation**: you want the agent to think about something, do a task, or respond in its own voice

## 1. Direct Message

```bash
openclaw message send \
  --channel <channel> \
  --target <target> \
  --message "<text>"
```

### Key options

| Flag | Purpose |
|------|---------|
| `--channel <name>` | Channel to use: telegram, discord, whatsapp, slack, signal, etc. |
| `--target <id>` | Recipient: chat ID, @username, phone number, channel ID (depends on channel) |
| `--message "<text>"` | Message body, supports standard markdown |
| `--media <path-or-url>` | Attach an image, audio, video, or document |
| `--silent` | Send without notification sound (Telegram + Discord) |
| `--reply-to <id>` | Reply to a specific message |
| `--json` | Output result as JSON |
| `--dry-run` | Preview payload without actually sending |

### Examples

```bash
# Simple text message
openclaw message send --channel telegram --target 12345678 --message "Build finished successfully"

# With media attachment
openclaw message send --channel telegram --target 12345678 --message "Screenshot" --media /tmp/screenshot.png

# Silent (no notification buzz)
openclaw message send --channel telegram --target 12345678 --message "FYI" --silent

# Dry run to verify before sending
openclaw message send --channel telegram --target 12345678 --message "Test" --dry-run --json
```

## 2. Agent Conversation

```bash
openclaw agent \
  --agent <agent-id> \
  --message "<prompt>" \
  --deliver \
  --channel <channel> \
  --reply-to <target> \
  --json
```

This sends `<prompt>` to an OpenClaw agent. The agent processes it using its full context (personality, memory, skills, tools) and the reply gets delivered to the specified chat.

### Finding your agent

```bash
# List available agents
openclaw agents list --json
```

The default agent is typically `main`.

### Key options

| Flag | Purpose |
|------|---------|
| `--agent <id>` | Target agent (use `openclaw agents list` to find available agents) |
| `--message "<text>"` | The prompt/message for the agent |
| `--deliver` | Send the agent's reply to the channel (without this, reply stays in CLI output only) |
| `--channel <name>` | Delivery channel |
| `--reply-to <target>` | Where to deliver the reply |
| `--thinking <level>` | Thinking level: off, minimal, low, medium, high, xhigh |
| `--session-id <id>` | Continue an existing session (preserves conversation context) |
| `--timeout <seconds>` | Override timeout (default 600s) |
| `--json` | Output result as JSON (includes usage stats, session ID, etc.) |

### Examples

```bash
# Ask the agent something, deliver reply to a chat
openclaw agent --agent main --message "Check if there are unread emails" \
  --deliver --channel telegram --reply-to 12345678 --json

# Continue a conversation in the same session
openclaw agent --agent main --session-id <previous-session-id> \
  --message "What about calendar?" \
  --deliver --channel telegram --reply-to 12345678 --json

# Without delivery (just get the response locally)
openclaw agent --agent main --message "Summarize today's activity" --json
```

### Reading the response

The `--json` output includes:
- `result.payloads[].text` — agent's reply text
- `result.meta.agentMeta.sessionId` — session ID (reuse for follow-up conversations)
- `result.meta.agentMeta.usage` — token usage stats
- `result.meta.durationMs` — how long the turn took

## Tips

- Always use `--json` so the output is parseable
- For multi-turn conversations, save and reuse the `sessionId` from the response
- The agent command has a default 600s timeout; for complex tasks, increase with `--timeout`
- Use `--dry-run` on `message send` to preview before actually sending
- The agent has access to all tools configured in its OpenClaw workspace (email, web search, cron, browser, etc.)
