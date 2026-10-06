# Schuetzt Hooks und Einstellungen vor Aenderungen per Shell (PreToolUse-Hook fuer Bash und PowerShell).
# Statt einer Liste verbotener Befehle gilt eine Liste erlaubter Lese-Befehle:
# Erwaehnt ein Befehl .claude (oder passt ein Platzhalter darauf), duerfen nur lesende Programme laufen.
# Exit 2 = Befehl blockieren, Exit 0 = erlauben.

. (Join-Path $PSScriptRoot '_common.ps1')

$raw = [Console]::In.ReadToEnd()
try {
  $data = $raw | ConvertFrom-Json
} catch {
  [Console]::Error.WriteLine("Hook block-guard-paths: Eingabe nicht lesbar. Aufruf blockiert.")
  exit 2
}

$cmd = [string]$data.tool_input.command
if (-not $cmd) { exit 0 }

$msg = "Blockiert: Hooks und Einstellungen aendert nur der Mensch. Dieser Befehl wuerde .claude/hooks oder die settings-Dateien veraendern."
$gitMsg = "Blockiert: Dieser git-Befehl ueberschreibt Dateien im Arbeitsverzeichnis (auch die Hooks). Das macht der Mensch."

function Get-Normal([string]$s) {
  return (($s -replace '["''`^+]', '') -replace '\\', '/').ToLower()
}

$norm = Get-Normal $cmd
$cwdNorm = if ($data.cwd) { Get-Normal ([string]$data.cwd) } else { '' }

$candidates = @('.claude', '.claude/hooks', '.claude/hooks/block-env.ps1', '.claude/settings.json',
  '.claude/settings.local.json', 'settings.local.json')

function Test-Trigger([string]$text) {
  if ($text -match '\.claude|claude~\d|settings\.local\.json') { return $true }
  foreach ($tok in ($text -split '[\s=,;|&(){}<>]+')) {
    if ($tok -match '[*?\[]') {
      foreach ($c in $candidates) {
        try { if ($c -like $tok) { return $true } } catch { return $true }
      }
    }
  }
  return $false
}

# git: erstes Wort nach den globalen Optionen
function Get-GitSub([string[]]$tokens) {
  $i = 0
  while ($i -lt $tokens.Count -and $tokens[$i].StartsWith('-')) {
    if ($tokens[$i] -cin @('-c', '-C', '--git-dir', '--work-tree', '--namespace', '--config-env') -and ($i + 1) -lt $tokens.Count) { $i += 2 } else { $i++ }
  }
  if ($i -ge $tokens.Count) { return $null }
  return [pscustomobject]@{ Name = $tokens[$i].ToLower(); Args = @($tokens | Select-Object -Skip ($i + 1)) }
}

# 2>&1 enthaelt ein &, das sonst als Befehlstrenner gelesen wuerde
$invocations = @(Get-Invocations ($cmd -replace '\d?>&\d', ''))

# 1. git-Befehle, die Dateien im Arbeitsverzeichnis ueberschreiben, auch ohne Pfad (immer pruefen)
foreach ($inv in $invocations) {
  if ($inv.Program -ne 'git') { continue }
  $sub = Get-GitSub @($inv.Arguments)
  if (-not $sub) { continue }
  $a = @(); foreach ($x in $sub.Args) { $a += $x.ToLower() }
  $bad = $false
  switch ($sub.Name) {
    'clean'    { $bad = $true }
    'apply'    { $bad = $true }
    'am'       { $bad = $true }
    'reset'    { if (($a -contains '--hard') -or ($a -contains '--merge') -or ($a -contains '--keep')) { $bad = $true } }
    'restore'  {
      # Gross-/Kleinschreibung zaehlt: -S ist --staged, -s ist --source, -W ist --worktree
      $orig = @($sub.Args)
      $staged = $false; $worktree = $false
      foreach ($x in $orig) {
        if (($x -ceq '--staged') -or ($x -cmatch '^-[A-Za-z]*S[A-Za-z]*$')) { $staged = $true }
        if (($x -ceq '--worktree') -or ($x -cmatch '^--source') -or ($x -cmatch '^-[A-Za-z]*[Ws][A-Za-z]*$')) { $worktree = $true }
      }
      if (-not ($staged -and -not $worktree)) { $bad = $true }
    }
    'checkout' { if (($a -contains '--') -or ($a -contains '.') -or ($a -contains '-f') -or ($a -contains '--force') -or (@($a | Where-Object { $_ -like '--source*' }).Count -gt 0)) { $bad = $true } }
    'stash'    { if (($a -contains 'pop') -or ($a -contains 'apply')) { $bad = $true } }
  }
  if ($bad) {
    [Console]::Error.WriteLine($gitMsg)
    exit 2
  }
}

# 2. Nennt der Befehl .claude oder liegt das Arbeitsverzeichnis darin, duerfen nur lesende Programme laufen
if (-not ((Test-Trigger $norm) -or (Test-Trigger $cwdNorm))) { exit 0 }

$t = $norm -replace '\d?>&\d', '' -replace '\d?>\s*(\$null|/dev/null|nul)\b', ''
if ($t -match '>') {
  [Console]::Error.WriteLine($msg)
  exit 2
}

$readOnly = @('cat', 'type', 'get-content', 'gc', 'more', 'less', 'head', 'tail', 'wc', 'ls', 'dir', 'gci', 'get-childitem',
  'tree', 'select-string', 'sls', 'grep', 'egrep', 'fgrep', 'rg', 'findstr', 'test-path', 'get-item', 'gi', 'get-filehash',
  'resolve-path', 'rvpa', 'get-location', 'pwd', 'echo', 'write-output', 'write-host', 'diff', 'fc', 'compare-object',
  'measure-object', 'select-object', 'sort-object', 'where-object', 'foreach-object', 'format-table', 'format-list',
  'ft', 'fl', 'out-string', 'out-host', 'select', 'where', 'measure', 'foreach', '%', '?',
  'cd', 'chdir', 'set-location', 'sl', 'pushd', 'popd', 'push-location', 'pop-location')

$gitReadOnly = @('status', 'diff', 'log', 'show', 'add', 'commit', 'ls-files', 'blame', 'grep', 'check-ignore', 'rev-parse',
  'cat-file', 'branch', 'remote', 'fetch', 'push', 'tag', 'shortlog', 'describe', 'show-ref', 'diff-tree', 'ls-tree',
  'rev-list', 'switch', 'pull')

foreach ($inv in $invocations) {
  $p = [string]$inv.Program
  $ok = $false
  if ($readOnly -contains $p) {
    $ok = $true
  } elseif ($p -eq 'git') {
    $sub = Get-GitSub @($inv.Arguments)
    $hasOutput = $false
    foreach ($x in @($inv.Arguments)) { if ($x.ToLower().StartsWith('--output')) { $hasOutput = $true } }
    if ((-not $sub) -or (($gitReadOnly -contains $sub.Name) -and (-not $hasOutput))) { $ok = $true }
  } elseif ($p -match '^\$_(\.|$)') {
    $ok = $true
  } elseif ($p -match '^\$[a-z_][a-z0-9_]*(\.[a-z0-9_]+)*(=.*)?$') {
    # Zuweisung an eine Variable ist erlaubt, ein Aufruf ueber eine Variable nicht
    $first = ''
    if (@($inv.Arguments).Count -gt 0) { $first = [string]@($inv.Arguments)[0] }
    if ($p.Contains('=') -or $first -match '^[+\-*/]?=$') { $ok = $true }
  }
  if (-not $ok) {
    [Console]::Error.WriteLine($msg)
    exit 2
  }
}
exit 0