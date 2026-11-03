# KnowTalk Agent Skill Sets (`.dsh/skills/`)

This directory holds the project's shared agent skills, vendored into the repo
so any clone gets the same workflow without extra install steps
(see `openspec/specs/engineering-workflow/spec.md`).

## Two skill sets, one division of labor

| Skill set | Answers | Use it for |
|---|---|---|
| **openspec** (`openspec/SKILL.md`) | **WHAT** to change | Proposing, designing, validating, archiving spec-driven changes |
| **superpowers** (`superpowers/skills/*`) | **HOW** to change | Implementation discipline: think → isolate → plan → test-first → debug → review → verify |

**Hard rule:** any change that alters externally observable behavior MUST first
go through an OpenSpec change. The `superpowers` skills only operate *within*
an already-approved change; they never replace the spec gate.

## Skill files
Each skill lives at `<skillset>/skills/<name>/SKILL.md` with YAML frontmatter
containing `name` (== directory name) and `description`. Loaders rely on this.

## Enforcement
See `SOURCES.md` for what is actually enforced vs. archived in this host, and
`docs/ENGINEERING_GATE.md` for the full gate description.
