---
name: test-driven-development
description: Write a failing test (or assertion) that encodes the requirement BEFORE implementing the fix/feature. Use for any logic with a checkable outcome.
---

# Test-Driven Development

Goal: prove the requirement with a test before the code exists, so "it works" is
evidence, not a feeling.

## When to use
Any change whose behavior can be checked programmatically — parsers, stores,
route handlers, transforms, math.

## Steps
1. **Encode the requirement as a test/assertion** — the smallest check that fails
   now and should pass after the change.
2. **Run it** — confirm it fails for the right reason (not a typo in the test).
3. **Implement minimally** until the test passes.
4. **Keep it** — the test becomes the regression guard; run it in the
   verification step.

## KnowTalk specifics
- For `utils/kiosk_store.py`, isolate by redirecting `ks._path = lambda n:
  os.path.join(tmp, n)` (KIOSK_DIR is hard-coded, never reads env) — do NOT
  write to real `data/kiosk/`.
- For `server/kiosk_routes.py`, use `aiohttp.test_utils.TestClient` against the
  app and assert on `{"code":0,"data":...}` responses.
- Prefer isolated, read-only checks; never require the 4GB main service or the
  GPU to run a unit test.
