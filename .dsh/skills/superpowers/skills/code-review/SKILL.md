---
name: code-review
description: Review changes for correctness, spec compliance, and regressions before marking work complete. Use after implementation, before verification.
---

# Code Review

Goal: catch defects and spec drift that tests may miss, before claiming done.

## Checklist
- **Spec compliance** — does the change match the OpenSpec change's
  proposal/design/tasks? Did observable behavior change without a change? (gate
  violation if so.)
- **Correctness** — off-by-one, None handling, async/await, resource leaks
  (open files, ports, processes), exception swallowing.
- **Environment fit** — uses the one valid interpreter
  (`C:\Users\24306\.conda\envs\livetalking\python.exe`); no edits to real
  `data/` in tests; respects 1050Ti/8G cabin limits.
- **Side effects** — did it touch personal dirs, delete files, or write secrets?
- **Readability** — names, comments where the "why" is non-obvious.

## How
- Re-read the diff (`git diff` / `git diff --cached`), don't trust summaries.
- For UI, reason about the event flow; for routes, trace the request path.
- If you find a real issue, fix it and re-verify; do not "note and ship".
