# Gemeinsame Hilfsfunktionen der Hook-Skripte. Wird per Punkt-Quelltext eingebunden.

# Programme, die einen Befehl als Argument ausfuehren (Huellen und Vorsaetze)
$script:Wrappers = @('bash', 'sh', 'zsh', 'dash', 'cmd', 'powershell', 'pwsh', 'iex', 'invoke-expression',
  'start-process', 'start', 'wsl', 'nohup', 'xargs', 'sudo', 'env', 'time', 'command', 'exec', 'call', 'builtin')

function Get-Tokens([string]$segment) {
  $found = [regex]::Matches($segment, '"[^"]*"|''[^'']*''|\S+')
  $result = @()
  foreach ($f in $found) {
    $t = $f.Value -replace '["''`^]', ''
    if ($t.Length -gt 0) { $result += $t }
  }
  return $result
}

# Zerlegt einen Befehl in alle Programmaufrufe, auch die in Huellen und Unterausdruecken.
function Get-Invocations([string]$cmd, [int]$depth = 0) {
  $result = @()
  if ($depth -gt 4) { return $result }
  $segments = $cmd -split '&&|\|\||[;|&\r\n(){}]'
  foreach ($segment in $segments) {
    $tokens = @(Get-Tokens $segment)
    $i = 0
    while ($i -lt $tokens.Count -and $tokens[$i] -match '^(&|\.|[A-Za-z_][A-Za-z0-9_]*=.*|-[A-Za-z]+|/[A-Za-z])$') { $i++ }
    if ($i -ge $tokens.Count) { continue }
    $leaf = ($tokens[$i] -replace '^.*[\\/]', '').ToLower() -replace '\.exe$', ''
    $rest = @($tokens | Select-Object -Skip ($i + 1))
    $result += [pscustomobject]@{ Program = $leaf; Arguments = $rest }
    if (($script:Wrappers -contains $leaf) -and ($rest.Count -gt 0)) {
      $inner = @(Get-Invocations ($rest -join ' ') ($depth + 1))
      foreach ($x in $inner) { if ($x) { $result += $x } }
    }
  }
  return $result
}