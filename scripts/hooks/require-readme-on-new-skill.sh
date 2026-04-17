#!/usr/bin/env bash
set -euo pipefail

# Enforce README updates when new skills are added to the repository.
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo '{"continue": true}'
  exit 0
fi

new_skill_files=""
while IFS= read -r file; do
  [[ -z "${file}" ]] && continue
  if ! git ls-files --error-unmatch "${file}" >/dev/null 2>&1; then
    new_skill_files+="${file}"$'\n'
  fi
done < <(git status --porcelain | awk '{print $2}' | grep -E '(^|/)SKILL\.md$' || true)

if [[ -z "${new_skill_files}" ]]; then
  echo '{"continue": true}'
  exit 0
fi

readme_changed="$(git status --porcelain -- README.md || true)"
if [[ -n "${readme_changed}" ]]; then
  echo '{"continue": true}'
  exit 0
fi

message="README.md must be updated when adding a new skill. New SKILL.md file(s): $(echo "${new_skill_files}" | tr '\n' ' ' | sed 's/[[:space:]]*$//')."

printf '{"decision":"block","stopReason":"%s","systemMessage":"%s"}\n' "${message}" "${message}"
exit 2