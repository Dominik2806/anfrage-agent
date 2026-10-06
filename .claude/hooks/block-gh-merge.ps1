# Blockiert Merge und schreibende GitHub-Aufrufe (PreToolUse-Hook fuer Bash und PowerShell).
# Exit 2 = Befehl blockieren, Exit 0 = erlauben.

$raw = [Console]::In.ReadToEnd()
try {
  $data = $raw | ConvertFrom-Json
} catch {
  [Console]::Error.WriteLine("Hook block-gh-merge: Eingabe nicht lesbar. Aufruf blockiert.")
  exit 2
}

$cmd = [string]$data.tool_input.command
if (-not $cmd) { exit 0 }

$msg = "Blockiert: Mergen, schreibende gh-api-Aufrufe und gh alias sind nicht erlaubt. Den Merge macht der Mensch im Browser."

function Get-Tokens([string]$segment) {
  $found = [regex]::Matches($segment, '"[^"]*"|''[^'']*''|\S+')
  $result = @()
  foreach ($f in $found) { $result += $f.Value.Trim([char]34, [char]39) }
  return $result
}

$segments = $cmd -split '&&|\|\||[;|&\r\n]'

foreach ($segment in $segments) {
  $tokens = @(Get-Tokens $segment)
  $i = 0
  while ($i -lt $tokens.Count -and $tokens[$i] -match '^(&|\.|call|[A-Za-z_][A-Za-z0-9_]*=.*)$') { $i++ }
  if ($i -ge $tokens.Count) { continue }

  $leaf = ($tokens[$i] -replace '^.*[\\/]', '').ToLower() -replace '\.exe$', ''
  if ($leaf -ne 'gh') { continue }

  $rest = @($tokens | Select-Object -Skip ($i + 1))
  if ($rest.Count -eq 0) { continue }
  $lower = @()
  foreach ($t in $rest) { $lower += $t.ToLower() }

  # gh pr ... merge
  $prIdx = [Array]::IndexOf($lower, 'pr')
  if ($prIdx -ge 0) {
    for ($j = $prIdx + 1; $j -lt $lower.Count; $j++) {
      if ($lower[$j] -eq 'merge') {
        [Console]::Error.WriteLine($msg)
        exit 2
      }
    }
  }

  # gh alias set / import
  if ($lower[0] -eq 'alias' -and $lower.Count -gt 1 -and @('set', 'import') -contains $lower[1]) {
    [Console]::Error.WriteLine($msg)
    exit 2
  }

  # gh api darf nur lesen: keine andere Methode als GET, keine Daten
  if ($lower[0] -eq 'api') {
    $method = $null
    $body = $false
    for ($j = 1; $j -lt $rest.Count; $j++) {
      $t = $rest[$j]
      if ($t -eq '-X' -or $t -eq '--method') {
        if (($j + 1) -lt $rest.Count) { $method = $rest[$j + 1] }
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