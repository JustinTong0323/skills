# openclaw-interact

A Claude Code plugin for interacting with [OpenClaw](https://docs.openclaw.ai/) — send messages via chat channels and talk to the OpenClaw agent directly from Claude Code.

## Features

- **Direct messaging** — Send messages to Telegram, Discord, WhatsApp, Slack, Signal, etc.
- **Agent conversation** — Talk to your OpenClaw agent and have replies delivered to any channel
- **Multi-turn sessions** — Continue conversations across multiple turns with session persistence

## Installation

```
/install-plugin github:JustinTong0323/skills:openclaw-interact
```

## Prerequisites

- [OpenClaw](https://docs.openclaw.ai/) installed and configured
- At least one channel set up (Telegram, Discord, etc.)
- Gateway running (`openclaw gateway`)

## Usage

Once installed, Claude Code will automatically use this skill when you say things like:

- "Send me a Telegram message saying the build is done"
- "Ask the agent to check my email"
- "Ping the agent"

## License

MIT
