# AGENTS

## Repository Status
- This repository is currently empty (no source files, configs, or docs yet).
- Treat this as a greenfield workspace.

## Agent Priorities
- Keep changes minimal and task-focused.
- Prefer established project conventions once they exist; do not invent broad standards unless needed for the requested task.
- Avoid large scaffolding unless the user explicitly asks for project setup.

## Command Discovery
- When code/config files appear, infer commands from the repo first:
  - JavaScript/TypeScript: `package.json` scripts
  - Python: `pyproject.toml`, `requirements*.txt`, `tox.ini`, `noxfile.py`
  - General: `Makefile`, `justfile`, CI workflows
- Run the narrowest relevant checks/tests for changed files before broader test runs.

## Editing Conventions
- Match existing style in touched files.
- Keep public APIs stable unless the task requires a change.
- Do not refactor unrelated code.
- Update docs when behavior changes.

## Documentation Strategy
- Link to existing docs instead of duplicating them in instructions.
- As project docs are added, keep this file concise and point to:
  - `README.md` for setup/run basics
  - `CONTRIBUTING.md` for development workflow
  - `docs/` for architecture and subsystem details

## When This File Should Be Updated
- After introducing a build/test toolchain
- After adding coding or linting standards
- After adding architecture docs or monorepo package boundaries