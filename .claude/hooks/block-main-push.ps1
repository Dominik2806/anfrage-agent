# Blockiert Pushes auf main/master (PreToolUse-Hook fuer Bash und PowerShell).
# Exit 2 = Befehl blockieren, Exit 0 = erlauben.

. (Join-Path $PSScriptRoot '_common.ps1')

$raw = [Console]::In.ReadToEnd()
try {
  $data = $raw | ConvertFrom-Json
} catch {
  [Console]::Error.WriteLine("Hook block-main-push: Eingabe nicht lesbar. Aufruf blockiert.")
  exit 2
}

$cmd = [string]$data.tool_input.command
if (-not $cmd) { exit 0 }

$msg = "Blockiert: Pushes auf main/master sind nicht erlaubt. Arbeite auf einem Feature-Branch und erstelle einen Pull Request."
$cwd = if ($data.cwd) { [string]$data.cwd } else { $env:CLAUDE_PROJECT_DIR }
if (-not $cwd) { $cwd = (Get-Location).Path }

function Stop-Blocked {
  [Console]::Error.WriteLine($msg)
  exit 2
}

$invocations = @(Get-Invocations $cmd)
foreach ($inv in $invocations) {
  if ($inv.Program.StartsWith('<')) { Stop-Blocked }
}

$pushes = @()
$switchedToMain = $false
foreach ($inv in $invocations) {
  if ($inv.Program -ne 'git') { continue }
  $sub = Get-GitSub @($inv.Arguments) $cwd
  if (-not $sub) { continue }
  foreach ($c in $sub.Config) { if ($c -match '^alias\.') { Stop-Blocked } }
  if (@('switch', 'checkout') -contains $sub.Name) {
    foreach ($a in $sub.Args) { if ($a -match '^(main|master)$') { $switchedToMain = $true } }
  }
  if ($sub.Name -eq 'push') { $pushes += $sub }
}

foreach ($sub in $pushes) {
  # Ziel pruefen: main, master, HEAD:main, +main, refs/heads/main, --all, --mirror, Platzhalter, Variablen
  foreach ($a in @($sub.Args)) {
    if ($a -match '^--(al|all|mir|mirr|mirro|mirror)$' -or $a -match '(^|[:/+,])(main|master)$' -or $a -match '[\\$*]') { Stop-Blocked }
  }
  # Im selben Befehl auf main gewechselt: der Branch zur Hook-Zeit ist nicht der Branch beim Push
  if ($switchedToMain) { Stop-Blocked }
  # Der aktuelle Branch gilt fuer jeden Push; ist er unklar, wird blockiert
  $branch = ''
  try { $branch = (& git -C $sub.Repo branch --show-current 2>$null) } catch { $branch = '' }
  if ([string]::IsNullOrWhiteSpace($branch) -or ($branch -match '^(main|master)$')) { Stop-Blocked }
}
exit 0