---
name: writing-plans
description: Produce a concrete, ordered, verifiable plan before implementing. Use after brainstorming, once the goal and approach are agreed.
---

# Writing Plans

Goal: turn an agreed approach into a step-by-step plan with a clear definition of
done, so implementation is mechanical and reviewable.

## When to use
Any task with 3+ distinct steps, or any task the user asked to "plan first".

## Structure
1. **Goal statement** — one line.
2. **Investigation** — files to read, commands to run, unknowns to resolve.
3. **Steps** — numbered, each with: action, the file(s) it touches, and how to
   verify it (command or assertion).
4. **Risks / rollback** — what could break and how to undo (e.g. backup before
   deleting personal files, branch so it can be abandoned).
5. **Definition of done** — the evidence that proves completion.

## Rules
- Keep steps small enough to verify independently.
- If a step reveals a new fork, stop and re-brainstorm; do not silently pick.
- The plan MUST respect the OpenSpec gate: if the work changes observable
  behavior, step 0 is "open/confirm the OpenSpec change".
- Prefer a TaskCreate checklist in-session for multi-step execution so progress
  is visible.
