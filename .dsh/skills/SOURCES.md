# Skill Sources & Enforcement Traceability

Records where the agent skill sets under `.dsh/skills/` come from and which parts
are actually enforced in this project, per
`openspec/specs/engineering-workflow/spec.md` ("引入范围与强制力来源必须被说明").

## 1. Skill sets present
- **`openspec/`** — project-specific skill describing how to drive the OpenSpec
  CLI for KnowTalk. Authored locally; wraps the installed `openspec` CLI
  (v1.14.1).
- **`superpowers/`** — 8 implementation-discipline skills: brainstorming,
  using-git-worktrees, writing-plans, subagent-driven-development,
  test-driven-development, systematic-debugging, code-review,
  verification-before-completion.

## 2. Upstream source (traceability)
The `superpowers` skills implement the public **Superpowers** agent methodology
(originated by Jesse Vincent, upstream `github.com/jessevictor/superpowers`,
MIT licensed). They are **original local implementations** of that methodology,
NOT a verbatim copy of an upstream commit. No third-party source code was
copied, so there is no upstream commit SHA to pin and the only license needed
is the MIT notice in `superpowers/LICENSE`.

To obtain the verbatim upstream skill files, clone the upstream repo and copy
its `skills/` directory; the methodology is equivalent.

## 3. What is ENFORCED here (effective)
- **Git pre-commit hook** (`scripts/git-hooks/pre-commit`, installed by
  `scripts/setup-governance.sh`) runs `openspec validate --all` and blocks the
  commit when specs or active changes are invalid. This is the real, working
  gate for "改行为先走 OpenSpec 变更".
- **In-session agent discipline**: the agent loads `using-superpowers` + the
  OpenSpec skill at the start of each task and self-checks the gate before
  writing code.

## 4. What is ARCHIVED / NOT enforced (honest limitation)
The `engineering-workflow` spec's "hook 硬性拦截" scenario (lines 141–169)
describes a host **PreToolUse hook** that intercepts Write/Bash calls to protect
`.dsh/skills/`. **WorkBuddy (this host) does not provide a PreToolUse hook
mechanism**, so that real-time interception is NOT available here. Consequences:
- Writes to `.dsh/skills/` are protected only by **convention** — do not edit
  vendored/upstream skill files in place; project conventions belong in `docs/`
  or `openspec/`.
- The gate is enforced at **commit time** (pre-commit hook) and by **agent
  discipline**, not at every tool call.
- Read-only operations on `.dsh/skills/` (ls, grep, cat, git status, backups to
  outside the repo) are always permitted.

To get the full upstream enforcement (real-time hooks), run the agent in a host
that supports PreToolUse hooks (e.g. Claude Code with the superpowers skill's
native hooks) and follow its install steps. Those host config files may contain
local paths/keys — review before committing them.

## 5. Upgrade steps
To refresh the superpowers methodology: re-read the upstream repo's skill files
and update the local `SKILL.md` bodies, keeping the YAML frontmatter
(`name`/`description`) intact and `name` == directory name. Update this file's
version note.
