---
name: telegram-notify
description: "Send Telegram bot notifications and adaptively poll for user replies, with per-job reply routing. Use when a workflow needs to notify the user at key checkpoints and react to replies without mixing responses from concurrent jobs."
---

# Telegram Notify

Send notifications and watch for user replies in the same Telegram chat. Supports multiple concurrent agents by routing Telegram replies to per-job inboxes: each agent only sees replies the user directed at it.

Uses an adaptive polling cadence — quiet when idle, faster right after the user posts something, then backs off smoothly to the idle rhythm.

## Prerequisites

Run `telegram-preflight` first. If env vars are not set, skip silently.

## Quick start

```bash
# Send a notification and register a job (returns job_id e.g. TEMP-1234-a3f2)
python3 scripts/notify.py send --job-id TEMP-1234 "🚀 [TEMP-1234] Started: doing the thing"

# Read replies for that job (without consuming)
python3 scripts/notify.py poll --read-inbox --job-id TEMP-1234-a3f2

# Consume (read + clear) replies for that job
python3 scripts/notify.py poll --consume-inbox --job-id TEMP-1234-a3f2

# Drain all pending updates and route them (one-shot, no inbox read)
python3 scripts/notify.py poll

# Start the adaptive watch loop (blocks — run in background or a separate terminal)
python3 scripts/notify.py watch &
```

## Subcommands

### `send` — deliver a notification

```bash
python3 scripts/notify.py send [--job-id TEMP-1234] "{MESSAGE}"
```

Reads message from stdin when called with no positional argument. Outputs JSON with `message_id`, `status`, and `job_id` (when `--job-id` was given).

Options:
- `--job-id TEMP-1234` — JIRA ticket or workflow label. The script appends a 4-char hex suffix (e.g. `TEMP-1234-a3f2`) and records the sent `message_id` → `job_id` in a shared registry. Save the returned `job_id` for later inbox reads.
- `--parse-mode` — Telegram parse mode (`Markdown` by default; pass `none` for plain text).

### `poll` — drain pending updates once

```bash
# Route new updates to per-job inboxes
python3 scripts/notify.py poll

# Read a specific job's inbox
python3 scripts/notify.py poll --read-inbox --job-id TEMP-1234-a3f2

# Read and clear a specific job's inbox
python3 scripts/notify.py poll --consume-inbox --job-id TEMP-1234-a3f2
```

One poller drains all pending updates (holding a file lock) and routes each reply to the correct job's private inbox based on Telegram's `reply_to_message_id`. Un-threaded messages are ignored silently. For each job that received replies, an intent summary is sent back as a Telegram reply to the user's last message, threaded in the same conversation.

Options:
- `--job-id` — required with `--read-inbox` / `--consume-inbox`.
- `--no-summary-notification` — skip the summary reply.

### `watch` — adaptive-cadence reply loop

```bash
python3 scripts/notify.py watch &
```

Runs continuously. The cadence adapts automatically:

| Situation | Next check in |
|-----------|--------------|
| No recent activity (idle) | 60 s |
| Reply detected | 15 s |
| No reply at 15 s | 30 s |
| No reply at 30 s | 45 s |
| No reply at 45 s | back to 60 s |

A new reply at any back-off step resets the cadence to 15 s. Stop with `Ctrl-C` or `kill`.

## Job routing — how multiple agents co-exist

```
Agent A: send --job-id TEMP-100 "..."  →  job_id: TEMP-100-a3f2, message_id: 101
Agent B: send --job-id TEMP-200 "..."  →  job_id: TEMP-200-9b1c, message_id: 102

User replies to message 101 → "ship it"
User replies to message 102 → "wait, recheck"

watch (shared) drains both replies, routes:
  message 101 reply → TEMP-100-a3f2 inbox
  message 102 reply → TEMP-200-9b1c inbox

Agent A: poll --consume-inbox --job-id TEMP-100-a3f2  →  gets "ship it"
Agent B: poll --consume-inbox --job-id TEMP-200-9b1c  →  gets "wait, recheck"
```

- Only **reply** messages are routed. Un-threaded messages are ignored.
- Registry entries expire after **24 hours**.
- One shared `watch` process handles routing for all jobs. Individual agents only consume their own inbox.

## Message templates

| Event | Template |
|-------|----------|
| Workflow started | `🚀 [{CONTEXT}] Started: {description}` |
| Waiting for user | `⏸️ [{CONTEXT}] Waiting for input — check the chat.` |
| Success | `✅ [{CONTEXT}] {description}` |
| Failure (auto-fix) | `🔴 [{CONTEXT}] {description}. Attempting auto-fix...` |
| Warning | `🟡 [{CONTEXT}] {description}` |
| Blocked | `🟡 [{CONTEXT}] Blocked: {reason}. Need your input.` |
| Ticket created | `🎫 [{CONTEXT}] JIRA {TICKET_ID}: {URL}` |
| PR submitted | `✅ [{CONTEXT}] PR submitted: {PR_URL}` |
| Revision pushed | `🔄 [{CONTEXT}] Revision pushed. Re-scan triggered.` |

Replace `{CONTEXT}` with the ticket ID or workflow name for traceability. Keep messages under 200 characters for mobile readability.

## Rules

- Never block a workflow because a notification or poll failed.
- Always check env vars before sending; skip silently if unset.
- Use Markdown parse mode for formatting (bold, inline code).
- Always reply to a specific bot message in Telegram; un-threaded messages are ignored.
- Save the `job_id` returned by `send --job-id` — it is required to read replies later.
- Run one shared `watch` process; do not start a `watch` per agent.
- A workflow must explicitly consume its inbox and branch on the result — `watch` does not automatically resume a paused agent session.
