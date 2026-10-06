# Blockiert Merge, Aenderungen an Repository, Secrets, Workflows und Aliassen sowie schreibende
# gh-api-Aufrufe (PreToolUse-Hook fuer Bash und PowerShell).
# Nicht erfasst sind andere gh-Befehle, zum Beispiel gh pr close oder gh pr review.
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

$msg = "Blockiert: Dieser gh-Befehl (Merge, Aenderung an Repository, Secrets, Workflows oder Aliassen, schreibender API-Aufruf) ist nicht erlaubt. Das macht der Mensch."

$blocked = @{
  'pr'        = @('merge')
  'repo'      = @('delete', 'edit', 'archive', 'rename')
  'secret'    = @('set', 'delete', 'remove')
  'variable'  = @('set', 'delete', 'remove')
  'workflow'  = @('run', 'enable', 'disable')
  'release'   = @('delete')
  'extension' = @('install', 'remove', 'upgrade')
  'alias'     = @('set', 'import', 'delete')
  'auth'      = @('token', 'logout', 'refresh')
}

foreach ($inv in @(Get-Invocations $cmd)) {
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
  if ($non.Count -ge 2 -and $blocked.ContainsKey($non[0]) -and ($blocked[$non[0]] -contains $non[1])) {
    [Console]::Error.WriteLine($msg)
    exit 2
  }

  # gh api darf nur lesen: keine andere Methode als GET, keine Daten
  if ($lower[0] -eq 'api') {
    $method = $null
    $body = $false
    for ($j = 1; $j -lt $ghArgs.Count; $j++) {
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
    if ($writes) {
      [Console]::Error.WriteLine($msg)
      exit 2
    }
  }
}
exit 0