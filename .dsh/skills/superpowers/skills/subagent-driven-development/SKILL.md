---
name: subagent-driven-development
description: Delegate independent, well-scoped pieces of work to subagents to parallelize and reduce context bloat. Use when a task has clearly separable parts.
---

# Subagent-Driven Development

Goal: split a large task into independent units and let subagents execute them
concurrently, then integrate and verify.

## When to use
- Multiple files/features with no shared mutable state between them.
- Broad exploration (codebase search, reading many files) that would bloat the
  main context.

## Steps
1. **Decompose** — list the independent units; each must be self-contained and
   verifiable on its own.
2. **Write a self-contained brief** for each subagent: goal, context already
   known, exact file paths, constraints (env interpreter, the OpenSpec gate),
   and the exact success criteria.
3. **Launch in parallel** when units are independent; sequential when later units
   depend on earlier output.
4. **Integrate & verify** — do not trust subagent "done" claims; re-run the
   verification yourself (tests, `openspec validate`, `py_compile`).
5. **Roll up** — summarize what actually changed; verify real files on disk.

## Gotchas
- Subagents cannot see the parent conversation; never pass vague instructions.
- Never delegate the OpenSpec gate decision; the parent still owns spec
  compliance.
