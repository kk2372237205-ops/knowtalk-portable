#!/usr/bin/env bash
# KnowTalk governance installer.
# Idempotent: installs the pre-commit gate into .git/hooks, backing up any
# pre-existing hook. Supports: install (default) and uninstall.
#
# Usage:
#   bash scripts/setup-governance.sh          # install
#   bash scripts/setup-governance.sh install  # same
#   bash scripts/setup-governance.sh uninstall
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$ROOT" ]; then
  echo "ERROR: not inside a git repository." >&2
  exit 1
fi

SRC="$ROOT/scripts/git-hooks/pre-commit"
TARGET="$ROOT/.git/hooks/pre-commit"
MARKER="# Managed by scripts/setup-governance.sh"

ACTION="${1:-install}"

if [ ! -f "$SRC" ]; then
  echo "ERROR: source hook not found at $SRC" >&2
  exit 1
fi

if [ "$ACTION" = "uninstall" ]; then
  if [ -f "$TARGET" ] && head -n 3 "$TARGET" | grep -qF "$MARKER"; then
    rm -f "$TARGET"
    echo "Removed KnowTalk pre-commit hook."
  else
    echo "No managed KnowTalk hook present; nothing to remove."
  fi
  # restore newest backup if available
  BACKUP="$(ls -1t "$ROOT/.git/hooks/pre-commit.backup."* 2>/dev/null | head -n 1)"
  if [ -n "$BACKUP" ]; then
    cp "$BACKUP" "$TARGET"
    echo "Restored backup: $(basename "$BACKUP")."
  fi
  exit 0
fi

# install
if [ -f "$TARGET" ] && ! head -n 3 "$TARGET" | grep -qF "$MARKER"; then
  TS="$(date +%Y%m%d-%H%M%S)"
  cp "$TARGET" "$TARGET.backup.$TS"
  echo "Backed up existing hook to pre-commit.backup.$TS"
fi

cp "$SRC" "$TARGET"
chmod +x "$TARGET"
echo "Installed KnowTalk pre-commit gate at .git/hooks/pre-commit"
echo "Run 'git commit' — the gate runs 'openspec validate --all' automatically."
