# ai-skills

Reusable AI agent skills for this repository and external users.

## Skill Location

Skills are stored at:

- `skills/<skill-name>/SKILL.md`

## Available Skills

- `gitlab-create-work-item` (`skills/gitlab-create-work-item/SKILL.md`): Draft the problem and requirements, then break them into tracer-bullet GitLab issues.
- `gitlab-work-item` (`skills/gitlab-work-item/SKILL.md`): Analyze an existing GitLab work item and produce a concrete solution plus execution plan.
- `grill-me` (`skills/grill-me/SKILL.md`): Relentless, one-question-at-a-time design/plan interview to stress-test decisions.
- `to-prd` (`skills/to-prd/SKILL.md`): Convert current conversation context into a structured PRD and submit it as a GitHub issue.
- `telegram-notify` (`skills/telegram-notify/SKILL.md`): Send Telegram notifications using env-based defaults.

## Install Skills

Use the Skills CLI:

```bash
npx skills@latest add <source>
```

`<source>` can be a GitHub repository (for example `owner/repo` or a full GitHub URL).

### Install All Skills From This Repository

Global install (all skills):

```bash
npx skills@latest add ./skills/ --all --global
```

Local install (all skills):

```bash
npx skills@latest add ./skills/ --all
```

### Install Locally (project scope)

Installs into your current project context.

```bash
npx skills@latest add <source>
```

Example:

```bash
npx skills@latest add <owner>/ai-skills
```

### Install Globally (user scope)

Installs for your user profile so skills are available across projects.

```bash
npx skills@latest add <source> --global
```

Example:

```bash
npx skills@latest add <owner>/ai-skills --global
```

## Useful Options

- `--agent <agents>`: install for specific agents
- `--skill <skills>`: install only selected skills from the source
- `--list`: show available skills without installing
- `--yes`: skip confirmation prompts

## Environment Variables

The `telegram-notify` skill expects these environment variables:

- `TELEGRAM_BOT_API_KEY`: Telegram bot API key used for authentication
- `TELEGRAM_BOT_CHAT_ID`: Default chat id (user, group, or channel) for notifications

Example setup:

```bash
export TELEGRAM_BOT_API_KEY="123456:example-token"
export TELEGRAM_BOT_CHAT_ID="-1001234567890"
```

## Maintainer Note

This repository includes a hook that enforces updating this README when new `SKILL.md` files are added.