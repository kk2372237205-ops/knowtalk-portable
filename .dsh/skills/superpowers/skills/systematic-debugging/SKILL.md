---
name: systematic-debugging
description: Find the root cause before changing code. Reproduce, isolate, form one hypothesis, test it; never guess-and-patch. Use whenever something is broken.
---

# Systematic Debugging

Goal: locate the true root cause so the fix is small and permanent, not a
whack-a-mole of symptoms.

## Method (in order)
1. **Reproduce** — get a deterministic command or action that shows the failure.
2. **Read the error** — full traceback, not just the last line. Identify the
   exact file:line and the call chain.
3. **Isolate** — bisect by questioning each link: is the input wrong? the
   function? the caller? the environment (interpreter, missing service)?
4. **One hypothesis at a time** — change ONE thing, re-run, observe. Do not pile
   up unrelated edits.
5. **Fix at the cause** — patch the layer that owns the invariant, not a caller
   that merely trips over it.
6. **Verify the original repro is gone** and no new failure appears.

## KnowTalk patterns seen before
- White screen → UI event pump never showed the login/connecting state because
  the unauthenticated branch only stored a pending flag and never rendered.
- `TypeError: make_btn(tooltip=)` → a param existed in one layer (`RoundButton`)
  but was never wired through `make_btn`.
- Silent no-audio (XTTS) → speaker id resolved once at init against a service
  that wasn't running, then failed *quietly*; fix = surface the error so the
  fallback engine triggers, and start the clone service automatically.
- Remember: clone service on port 50000 must be up for reference-audio voice;
  main service needs >4GB free RAM.
