---
name: using-git-worktrees
description: Isolate in-progress work so the main tree stays clean and experiments can be discarded. Use before starting a non-trivial change.
---

# Using Git Worktrees (and the KnowTalk equivalent)

Goal: keep unfinished work off the main working tree so you can switch context
or abandon an experiment without polluting the repo.

## KnowTalk project convention
This repo uses a **single working tree** (initialized for git in this governance
setup). For isolation we rely on:
- **OpenSpec change directories** (`openspec/changes/<name>/`) to isolate the
  *spec* work for each feature, and
- **git branches** for the *code* work (`git checkout -b feat/<name>`).

Use a true `git worktree` only when you must run two checkouts of the repo
simultaneously (e.g. build in one while editing in another).

## Steps (branch-based isolation)
1. `git status` — ensure clean tree (the pre-commit hook will block dirty specs).
2. `git checkout -b feat/<change-name>` from the latest `main`/`master`.
3. Do the work; commit logically.
4. Open/keep the matching OpenSpec change; `openspec validate --all` must pass.
5. On merge, delete the branch; archive the OpenSpec change (`openspec archive`).

## Why
Isolation makes "verification before completion" trustworthy — you can run the
full test/validate suite on a tree that contains only this change.
