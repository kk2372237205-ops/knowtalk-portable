# KnowTalk governance installer (PowerShell variant).
# Mirrors scripts/setup-governance.sh: idempotent install/uninstall of the
# pre-commit gate into .git/hooks, with backup of any pre-existing hook.
param(
  [ValidateSet("install","uninstall")]
  [string]$Action = "install"
)

$ErrorActionPreference = "Stop"
$ROOT = & git rev-parse --show-toplevel
if (-not $ROOT) { Write-Error "not inside a git repository"; exit 1 }

$SRC = Join-Path $ROOT "scripts/git-hooks/pre-commit"
$TARGET = Join-Path $ROOT ".git/hooks/pre-commit"
$MARKER = "# Managed by scripts/setup-governance.sh"

if (-not (Test-Path $SRC)) { Write-Error "source hook not found at $SRC"; exit 1 }

if ($Action -eq "uninstall") {
  $head = if (Test-Path $TARGET) { Get-Content $TARGET -First 3 } else { @() }
  if ($head -match [regex]::Escape($MARKER)) {
    Remove-Item $TARGET -Force
    Write-Host "Removed KnowTalk pre-commit hook."
  } else {
    Write-Host "No managed KnowTalk hook present; nothing to remove."
  }
  $backup = Get-ChildItem "$TARGET.backup.*" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($backup) {
    Copy-Item $backup.FullName $TARGET
    Write-Host "Restored backup: $($backup.Name)."
  }
  exit 0
}

# install
$head = if (Test-Path $TARGET) { Get-Content $TARGET -First 3 } else { @() }
if ($head -notmatch [regex]::Escape($MARKER)) {
  $ts = Get-Date -Format "yyyyMMdd-HHmmss"
  Copy-Item $TARGET "$TARGET.backup.$ts" -Force
  Write-Host "Backed up existing hook to pre-commit.backup.$ts"
}
Copy-Item $SRC $TARGET -Force
Write-Host "Installed KnowTalk pre-commit gate at .git/hooks/pre-commit"
