---
name: verification-before-completion
description: Do not claim a task done without evidence. Run the actual checks (tests, py_compile, openspec validate, manual repro) and report what passed. Use as the final step of every task.
---

# Verification Before Completion

Goal: "it should work" is not acceptable. Show the evidence.

## Required evidence (pick what applies)
- **Syntax/compile** — `python -m py_compile <files>` for every edited `.py`.
- **Tests** — the TDD test from earlier passes; run the isolated check.
- **Spec gate** — `openspec validate --all` is green for the touched change.
- **Manual repro** — for UI/behavior, describe the exact steps and the observed
  correct result; if you cannot run it (no GPU/cabin), say so explicitly.
- **No collateral damage** — confirm you did not modify personal dirs, secrets,
  or unrelated files.

## Reporting
State plainly: what was changed, what was verified, and anything that remains
unverified (and why). Never present unrun commands as proof. If a check cannot
be run in this environment, name the limitation rather than hiding it.
