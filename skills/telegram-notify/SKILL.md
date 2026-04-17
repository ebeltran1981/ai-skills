---
name: telegram-notify
description: Send Telegram notifications reliably for scripts, apps, and one-off alerts using environment-based defaults.
argument-hint: What notification text should be sent, and do you need to override default env-based routing?
---

# Telegram Notify

Use this skill when Telegram is used as a notification system.

## Outcome

Produce a working notification implementation (or one-off command) that:
- Uses Telegram Bot API `sendMessage`
- Uses `TELEGRAM_BOT_API_KEY` for auth
- Uses `TELEGRAM_BOT_CHAT_ID` as the default destination chat
- Verifies delivery with response checks
- Handles common API failures clearly

## Inputs To Collect

Collect these values before implementation:
- `TELEGRAM_BOT_API_KEY` (required): bot API key
- `TELEGRAM_BOT_CHAT_ID` (required): default chat id, group id, or channel id
- `text` (required): notification body
- Optional runtime overrides: `chat_id`, `parse_mode`, `disable_web_page_preview`, `disable_notification`, `message_thread_id`

If required environment variables are missing, ask for them before proceeding.

## Decision Flow

1. Choose delivery mode:
- One-off/manual send: use `curl` command.
- Application integration: implement in project language (Node, Python, etc.).

2. Choose credential and target source:
- Default to environment values:
  - `TELEGRAM_BOT_API_KEY`
  - `TELEGRAM_BOT_CHAT_ID`
- If project has a secrets manager, it may populate these env vars.

3. Choose formatting mode:
- If rich formatting is requested, use `parse_mode` and escape content correctly.
- If formatting issues appear, fall back to plain text.

4. Validate target:
- Use `TELEGRAM_BOT_CHAT_ID` unless the user explicitly overrides `chat_id`.
- If API returns `chat not found` or `bot was blocked`, report an actionable next step.

## Implementation Pattern

### Minimal API contract

- Endpoint: `https://api.telegram.org/bot<TOKEN>/sendMessage`
- Method: `POST`
- JSON body:
  - `chat_id` (required)
  - `text` (required)
  - `parse_mode` (optional)
  - `disable_web_page_preview` (optional)
  - `disable_notification` (optional)

### One-off command template

```bash
curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_API_KEY}/sendMessage" \
  -H "Content-Type: application/json" \
  -d '{
    "chat_id": "'"${TELEGRAM_BOT_CHAT_ID}"'",
    "text": "<NOTIFICATION_TEXT>",
    "disable_web_page_preview": true
  }'
```

When generating a command, keep `TELEGRAM_BOT_API_KEY` and `TELEGRAM_BOT_CHAT_ID` as environment variables.

### Integration requirements

- Build a reusable function/module, for example `sendTelegramNotification(text, options)`.
- Resolve defaults from env vars:
  - `apiKey = process.env.TELEGRAM_BOT_API_KEY`
  - `chatId = options.chatId || process.env.TELEGRAM_BOT_CHAT_ID`
- Validate required values and throw or return structured errors.
- Log non-sensitive diagnostic details (`status`, Telegram `description`, request context).
- Never log full token/API key.

## Error Handling Checklist

For non-2xx or `{ "ok": false }` responses:
- Surface Telegram `error_code` and `description`
- Classify likely cause:
  - `401`: invalid API key
  - `400`: bad request (invalid chat id, malformed parse mode payload)
  - `403`: bot blocked or unauthorized target
  - `429`: rate limit (apply backoff and retry)
- Provide a concrete remediation step per class.

## Completion Checks

A task is complete only when all apply:
- `TELEGRAM_BOT_API_KEY` is used for auth
- `TELEGRAM_BOT_CHAT_ID` is used for default chat routing
- Notification send path is implemented with `POST /sendMessage`
- At least one successful send is confirmed (or command is fully ready to run with provided inputs)
- Error path behavior is defined and user-visible
- Any new config/env usage is documented in relevant project docs

## Guardrails

- Do not commit secrets or real API keys.
- Do not expose API key values in logs, terminal output, or patches.
- Prefer the smallest viable implementation over framework-heavy abstractions.
- Keep changes scoped to the user request.

## Example Prompts This Skill Should Handle

- "Add a Python notifier that uses `TELEGRAM_BOT_API_KEY` and `TELEGRAM_BOT_CHAT_ID`."
- "Create a Node utility to send deployment notifications to Telegram with env-based defaults."
- "Generate a one-off curl command that sends a release notification using my Telegram env vars."