# Gemeinsame Hilfsfunktionen der Hook-Skripte. Wird per Punkt-Quelltext eingebunden.

# Programme, die einen Befehl als Argument ausfuehren (Huellen und Vorsaetze)
$script:Wrappers = @('bash', 'sh', 'zsh', 'dash', 'cmd', 'powershell', 'pwsh', 'iex', 'invoke-expression', 'eval',
  'start-process', 'saps', 'start', 'wsl', 'nohup', 'xargs', 'sudo', 'doas', 'runas', 'env', 'time', 'command',
  'exec', 'call', 'builtin', 'timeout', 'nice', 'ionice', 'setsid', 'stdbuf', 'watch', 'invoke-command', 'icm',
  'start-job', 'sajb', 'invoke-item', 'ii')

# Marker stehen fuer Aufrufe, die ein Textabgleich nicht verlaesslich lesen kann. Die Hooks blockieren sie.
function New-Marker([string]$name) {
  return [pscustomobject]@{ Program = $name; Arguments = @() }
}

function Get-Tokens([string]$segment) {
  $found = [regex]::Matches($segment, '"[^"]*"|''[^'']*''|\S+')
  $result = @()
  foreach ($f in $found) {
    $t = $f.Value -replace '["''`^]', ''
    if ($t.Length -gt 0) { $result += $t }
  }
  return $result
}

# Zerlegt einen Befehl in alle Programmaufrufe, auch die in Huellen, Unterausdruecken und Zuweisungen.
function Get-Invocations([string]$cmd, [int]$depth = 0) {
  $result = @()
  if ($depth -gt 4) { return @(New-Marker '<zu-tief>') }
  if ($depth -eq 0) {
    if (($cmd -match '(?i)\b(iex|invoke-expression|eval)\b\s*[\(\$\[`]') -or ($cmd -match '(?i)\|\s*(iex|invoke-expression)\b') -or ($cmd -match '(?i)\[scriptblock\]')) {
      $result += New-Marker '<dynamisch>'
    }
  }
  $segments = $cmd -split '&&|\|\||[;|&\r\n(){}`]'
  foreach ($segment in $segments) {
    # Zuweisung an eine Variable: der Text nach dem = ist der eigentliche Befehl
    $work = $segment
    for ($g = 0; $g -lt 3; $g++) {
      $m = [regex]::Match($work, '^\s*\$[A-Za-z_][\w:.]*\s*(?:\+|-|\*|/)?=\s*(.*)$', 'Singleline')
      if (-not $m.Success) { break }
      $work = $m.Groups[1].Value
      if ($work -match '^\s*["'']') { $work = ''; break }
    }
    if ($work.Trim().Length -eq 0) { continue }

    $tokens = @(Get-Tokens $work)
    $i = 0
    while ($i -lt $tokens.Count -and $tokens[$i] -match '^(&|\.|[A-Za-z_][A-Za-z0-9_]*=.*|-[A-Za-z]+|/[A-Za-z]|\d+(\.\d+)?[smhd]?)$') { $i++ }
    if ($i -ge $tokens.Count) { continue }
    $leaf = ($tokens[$i] -replace '^.*[\\/]', '').ToLower() -replace '\.(exe|cmd|bat|com)$', ''
    $rest = @($tokens | Select-Object -Skip ($i + 1))

    # Aufruf ueber eine Variable: das Programm steht nicht im Text
    if ($leaf.StartsWith('$') -and ($leaf -notmatch '^\$_') -and $rest.Count -gt 0) {
      $result += New-Marker '<dynamisch>'
      continue
    }

    $result += [pscustomobject]@{ Program = $leaf; Arguments = $rest }

    if (@('set-alias', 'new-alias', 'sal', 'nal') -contains $leaf) { $result += New-Marker '<alias>' }
    if (@('powershell', 'pwsh') -contains $leaf) {
      foreach ($r in $rest) {
        if ($r -match '^-(e|ec|en|enc|enco|encod|encode|encoded|encodedc|encodedco|encodedcom|encodedcomm|encodedcomma|encodedcomman|encodedcommand)$') {
          $result += New-Marker '<kodiert>'
          break
        }
      }
    }
    if (($script:Wrappers -contains $leaf) -and ($rest.Count -gt 0)) {
      if (@('start-process', 'saps', 'start') -contains $leaf) {
        $rest = @($rest | Where-Object { $_ -notmatch '^-(argumentlist|args|filepath|wait|passthru|nonewwindow)$' } | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
      }
      $inner = @(Get-Invocations ($rest -join ' ') ($depth + 1))
      foreach ($x in $inner) { if ($x) { $result += $x } }
    }
  }
  return $result
}

# git: Unterbefehl nach den globalen Optionen, dazu -c-Werte und das Repository (-C)
function Get-GitSub([string[]]$tokens, [string]$cwd = '') {
  $i = 0
  $config = @()
  $repo = $cwd
  while ($i -lt $tokens.Count -and $tokens[$i].StartsWith('-')) {
    $o = $tokens[$i]
    if ($o -ceq '-C' -and ($i + 1) -lt $tokens.Count) {
      $p = $tokens[$i + 1]
      try {
        if ([System.IO.Path]::IsPathRooted($p)) { $repo = $p } elseif ($repo) { $repo = Join-Path $repo $p } else { $repo = $p }
      } catch { }
      $i += 2
      continue
    }
    if ($o -ceq '-c' -and ($i + 1) -lt $tokens.Count) { $config += $tokens[$i + 1]; $i += 2; continue }
    if ($o -cin @('--git-dir', '--work-tree', '--namespace', '--config-env') -and ($i + 1) -lt $tokens.Count) { $i += 2; continue }
    if ($o -match '^--config-env=') { $config += $o }
    $i++
  }
  if ($i -ge $tokens.Count) { return $null }
  return [pscustomobject]@{ Name = $tokens[$i].ToLower(); Args = @($tokens | Select-Object -Skip ($i + 1)); Config = $config; Repo = $repo }
}