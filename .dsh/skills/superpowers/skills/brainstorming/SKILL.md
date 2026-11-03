---
name: brainstorming
description: Clarify requirements, constraints, and tradeoffs with the user BEFORE writing any code or opening an implementation plan. Use at the very start of any non-trivial task.
---

# Brainstorming

Goal: reach a shared, concrete understanding of the problem before committing to
a solution. Do NOT write code during brainstorming.

## When to use
- A task is ambiguous, multi-step, or has more than one reasonable approach.
- The user says "continue", "do X", or "fix Y" without specifying scope.

## Steps
1. **Restate the goal** in one sentence to confirm you understood it.
2. **Surface assumptions** — list what you are assuming about the environment,
   data shapes, dependencies, and user intent.
3. **Identify the fork** — if there are 2+ materially different approaches,
   present them as a short decision question (use AskUserQuestion) with the
   recommended option first and a clear "what each involves".
4. **Capture constraints** — performance, hardware (e.g. 1050Ti/8G cabin),
   API limits, backward compatibility, and the OpenSpec gate (does this change
   observable behavior? then a change MUST be opened first).
5. **Agree on the definition of done** — what evidence proves it works.

## Output
A crisp summary the user can correct, plus (if behavior-changing) a note that
you will open an OpenSpec change before coding. Keep it short; do not over-ask.
