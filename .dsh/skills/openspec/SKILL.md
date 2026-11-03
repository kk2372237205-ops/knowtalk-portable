---
name: openspec
description: Use the OpenSpec CLI to propose, design, validate, and archive spec-driven changes for KnowTalk. Trigger whenever a change will alter externally observable behavior (REST routes, CLI, UI, stored config, agent/skill behavior).
---

# OpenSpec Workflow (KnowTalk)

OpenSpec keeps capability specs (`openspec/specs/<cap>/spec.md`) and change
proposals (`openspec/changes/<name>/`) in the repo. It answers **WHAT to change**.
The `superpowers` skill set (`.dsh/skills/superpowers/`) answers **HOW to change**.

## When to open a change (the gate)
Open a change **BEFORE** editing code whenever the work changes externally
observable behavior:
- New or modified REST routes / API contracts (`server/kiosk_routes.py`, `app.py`)
- New CLI flags or commands
- UI/UX behavior changes visible to users (`desktop_app.py`)
- New stored config keys or JSON shapes under `data/`
- New or changed agent/skill behavior

Pure refactors with **no** observable behavior change do not need a change, but
should still be noted in the commit message.

## Commands
- `openspec list` / `openspec status <name>` — see active changes
- `openspec change <name>` — scaffold proposal/design/tasks + specs delta
- `openspec validate --all` — validate specs + active changes (used by pre-commit)
- `openspec archive <name> -y` — merge delta into the capability spec, move to
  `openspec/changes/archive/`

## Change anatomy
```
openspec/changes/<name>/
  proposal.md          # why + scope + impact
  design.md            # approach, tradeoffs, data flow
  tasks.md             # ADDED/CHANGED/REMOVED task checkboxes
  specs/<cap>/spec.md  # delta: ## ADDED / CHANGED / REMOVED Requirements
```
Delta uses `## ADDED Requirements` → `### Requirement` → `#### Scenario` with
WHEN/THEN/AND.

## Rules
- A change MUST pass `openspec validate --all` before archive.
- The git pre-commit hook enforces this; a failing change blocks the commit.
- Never hand-edit `openspec/specs/<cap>/spec.md` for a behavior change — go
  through a change + archive so history stays traceable.
- `superpowers` skills (how to implement) MUST NOT bypass this gate.
