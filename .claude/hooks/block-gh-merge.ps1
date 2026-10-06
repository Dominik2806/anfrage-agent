# Blockiert Merge, Review-Freigaben, Aenderungen an Repository, Secrets, Workflows, Konfiguration und Aliassen
# sowie schreibende gh-api-Aufrufe (PreToolUse-Hook fuer Bash und PowerShell).
# Nicht erfasst sind andere gh-Befehle; die Liste unten ist bewusst begrenzt.
# Exit 2 = Befehl blockieren, Exit 0 = erlauben.

. (Join-Path $PSScriptRoot '_common.ps1')

$raw = [Console]::In.ReadToEnd()
try {
  $data = $raw | ConvertFrom-Json
} catch {
  [Console]::Error.WriteLine("Hook block-gh-merge: Eingabe nicht lesbar. Aufruf blockiert.")
  exit 2
}

$cmd = [string]$data.tool_input.command
if (-not $cmd) { exit 0 }

$msg = "Blockiert: Dieser gh-Befehl (Merge, Freigabe, Aenderung an Repository, Secrets, Workflows, Konfiguration oder Aliassen, schreibender API-Aufruf) ist nicht erlaubt. Das macht der Mensch."

function Stop-Blocked {
  [Console]::Error.WriteLine($msg)
  exit 2
}

$blocked = @{
  'pr'        = @('merge', 'review', 'close', 'edit', 'ready')
  'issue'     = @('delete', 'transfer')
  'repo'      = @('delete', 'edit', 'archive', 'unarchive', 'rename', 'create', 'fork', 'sync')
  'secret'    = @('set', 'delete', 'remove')
  'variable'  = @('set', 'delete', 'remove')
  'workflow'  = @('run', 'enable', 'disable')
  'release'   = @('create', 'delete', 'edit', 'upload')
  'run'       = @('cancel', 'delete', 'rerun')
  'config'    = @('set')
  'extension' = @('install', 'remove', 'upgrade', 'exec', 'create')
  'alias'     = @('set', 'import', 'delete')
  'auth'      = @('token', 'logout', 'refresh', 'login', 'switch')
}

$invocations = @(Get-Invocations $cmd)
foreach ($inv in $invocations) {
  if ($inv.Program.StartsWith('<')) { Stop-Blocked }
}

foreach ($inv in $invocations) {
  if ($inv.Program -ne 'gh') { continue }
  $ghArgs = @($inv.Arguments)
  if ($ghArgs.Count -eq 0) { continue }
  $lower = @()
  foreach ($t in $ghArgs) { $lower += $t.ToLower() }

  # Gruppe und Aktion: die ersten zwei Woerter ohne Optionen (Werte von -R werden uebersprungen)
  $non = @()
  $j = 0
  while ($j -lt $lower.Count -and $non.Count -lt 2) {
    $t = $lower[$j]
    if (@('-r', '--repo', '--hostname') -contains $t) { $j += 2; continue }
    if ($t.StartsWith('-')) { $j++; continue }
    $non += $t
    $j++
  }
  if ($non.Count -ge 2 -and $blocked.ContainsKey($non[0]) -and ($blocked[$non[0]] -contains $non[1])) { Stop-Blocked }

  # gh api darf nur lesen: keine andere Methode als GET, keine Daten
  if ($non.Count -ge 1 -and $non[0] -eq 'api') {
    $method = $null
    $body = $false
    for ($j = 0; $j -lt $ghArgs.Count; $j++) {
      $t = $ghArgs[$j]
      if ($t -eq '-X' -or $t -eq '--method') {
        if (($j + 1) -lt $ghArgs.Count) { $method = $ghArgs[$j + 1] }
        continue
      }
      if ($t -match '^--method=(.+)$') { $method = $Matches[1]; continue }
      if ($t -match '^-X(.+)$') { $method = $Matches[1]; continue }
      if ($t -match '^(-f|-F|--field|--raw-field|--input)(=.*)?$' -or $t -match '^-[fF].+') { $body = $true }
    }
    $writes = $false
    if ($method) { if ($method.ToUpper() -ne 'GET') { $writes = $true } }
    elseif ($body) { $writes = $true }
    if ($writes) { Stop-Blocked }
  }
}
exit 0