---
name: gitlab-work-item
description: Analyze an existing GitLab work item from its number, then produce a concrete solution and execution plan. Use when user already has requirements and needs implementation planning.
argument-hint: GitLab work item number (IID) or URL to analyze.
---

# GitLab Work Item Analysis And Plan

Use this skill to turn a GitLab work item into an actionable implementation plan.

## Outcome

Produce a plan that includes:
- Problem understanding from the work item description
- Assumptions, unknowns, and risks
- Proposed technical approach
- Ordered implementation steps with dependencies
- Validation and testing strategy

## Inputs

Required:
- GitLab work item IID (or full URL)

Optional:
- Target project (`group/project`) if the current `glab` context is not the right repository
- Constraints (deadline, architecture preferences, scope limits)

## Process

### 1. Resolve and fetch the work item

- If input is a URL, extract IID from it.
- Fetch the item with comments:
  - Default project context: `glab issue view <iid> --comments`
  - Explicit project context: `glab issue view <iid> -R <group/project> --comments`
- If fetch fails, explain what is missing (auth, project context, permissions, invalid IID).

### 2. Analyze the work item

Extract and summarize:
- Objective and expected user/business outcome
- Functional requirements and non-functional requirements
- Constraints, dependencies, and integration touchpoints
- Acceptance criteria (explicit or inferred)

### 3. Identify gaps and assumptions

List:
- Missing details that could block implementation
- Assumptions required to continue
- Risks and mitigations

If critical information is missing, ask concise follow-up questions before finalizing the plan.

### 4. Build the implementation plan

Create an ordered plan with:
- Milestones/phases
- Concrete tasks per phase
- Dependency order and parallelizable items
- Expected outputs/deliverables per phase

Prefer thin, verifiable increments over large monolithic tasks.

### 5. Add validation strategy

Include:
- How to validate each major phase
- Test levels (unit/integration/e2e as applicable)
- Release/readiness checks

## Output Format

Return sections in this order:

1. **Work Item Summary**
2. **Assumptions And Open Questions**
3. **Risks And Mitigations**
4. **Implementation Plan**
5. **Validation Plan**

## Completion Checks

A run is complete only when:
- The work item was fetched from GitLab (or blocker was clearly reported)
- Description and comments were considered in the analysis
- Plan is ordered and dependency-aware
- Validation approach is included
- Open questions are explicitly captured

## Example Prompts

- "Use gitlab-work-item for issue 142 and give me a full implementation plan."
- "Analyze https://gitlab.example.com/group/proj/-/issues/87 and produce milestones."
- "Use gitlab-work-item with issue 33 in `mygroup/myrepo` and include testing strategy."