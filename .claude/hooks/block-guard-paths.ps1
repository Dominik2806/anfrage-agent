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
$gitMsg = "Blockiert: Dieser Befehl ueberschreibt oder loescht Dateien im Arbeitsverzeichnis (auch die Hooks). Das macht der Mensch."

function Stop-Blocked([string]$text) {
  [Console]::Error.WriteLine($text)
  exit 2
}

# Anfuehrungszeichen, Gravis, ^ und + entfernen; die zweite Fassung entfernt auch Backslashes (Bash-Escapes)
$base = ($cmd -replace '["''`^+]', '').ToLower()
$norm = $base -replace '\\', '/'
$norm2 = $base -replace '\\', ''
$cwdBase = if ($data.cwd) { ([string]$data.cwd -replace '["''`^+]', '').ToLower() } else { '' }
$cwdNorm = $cwdBase -replace '\\', '/'

# Kandidaten fuer Platzhalter: alle Dateien im Hook-Ordner und die Einstellungsdateien
$candidates = @('.claude', '.claude/hooks', '.claude/settings.json', '.claude/settings.local.json', 'settings.local.json')
foreach ($f in @(Get-ChildItem -LiteralPath $PSScriptRoot -File -ErrorAction SilentlyContinue)) {
  $candidates += ('.claude/hooks/' + $f.Name.ToLower())
}

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

$invocations = @(Get-Invocations ($cmd -replace '\d?>&\d', ''))

# 0. Aufrufe, die ein Textabgleich nicht lesen kann
foreach ($inv in $invocations) {
  if ($inv.Program.StartsWith('<')) { Stop-Blocked $msg }
}

# 1. Befehle, die Dateien im Arbeitsverzeichnis ueberschreiben oder loeschen, auch ohne Pfad (immer pruefen)
$deleters = @('remove-item', 'ri', 'rm', 'del', 'erase', 'rd', 'rmdir', 'robocopy', 'xcopy')
foreach ($inv in $invocations) {
  $p = [string]$inv.Program
  if ($deleters -contains $p) {
    foreach ($x in @($inv.Arguments)) {
      if (@('.', './', '.\', '..', '../', '*', '$pwd') -contains $x.ToLower()) { Stop-Blocked $gitMsg }
    }
  }
  if ($p -ne 'git') { continue }
  $sub = Get-GitSub @($inv.Arguments) ''
  if (-not $sub) { continue }
  $a = @(); foreach ($x in $sub.Args) { $a += $x.ToLower() }
  $bad = $false
  switch ($sub.Name) {
    'clean'          { $bad = $true }
    'apply'          { $bad = $true }
    'am'             { $bad = $true }
    'checkout-index' { $bad = $true }
    'read-tree'      { $bad = $true }
    'rm'             { if (($a -contains '.') -or ($a -contains './') -or ($a -contains '*') -or (@($a | Where-Object { $_ -match '^-[a-z]*r[a-z]*f|^-[a-z]*f[a-z]*r' }).Count -gt 0)) { $bad = $true } }
    'reset'          { if (@($a | Where-Object { $_ -match '^--(ha(r(d)?)?|me(r(g(e)?)?)?|ke(e(p)?)?)$' }).Count -gt 0) { $bad = $true } }
    'restore'        {
      # Gross-/Kleinschreibung zaehlt: -S ist --staged, -s ist --source, -W ist --worktree
      $staged = $false; $worktree = $false
      foreach ($x in @($sub.Args)) {
        if (($x -ceq '--staged') -or ($x -cmatch '^-[A-Za-z]*S[A-Za-z]*$')) { $staged = $true }
        if (($x -ceq '--worktree') -or ($x -cmatch '^--source') -or ($x -cmatch '^-[A-Za-z]*[Ws][A-Za-z]*$')) { $worktree = $true }
      }
      if (-not ($staged -and -not $worktree)) { $bad = $true }
    }
    'checkout'       { if (($a -contains '--') -or ($a -contains '.') -or ($a -contains './') -or ($a -contains ':/') -or ($a -contains '--force') -or ($a -contains '--ours') -or ($a -contains '--theirs') -or (@($a | Where-Object { $_ -match '^-[a-z]*f[a-z]*$|^--source' }).Count -gt 0)) { $bad = $true } }
    'switch'         { if (($a -contains '--force') -or ($a -contains '--discard-changes') -or (@($a | Where-Object { $_ -match '^-[a-z]*f[a-z]*$' }).Count -gt 0)) { $bad = $true } }
    'stash'          { if (($a -contains 'pop') -or ($a -contains 'apply')) { $bad = $true } }
  }
  if ($bad) { Stop-Blocked $gitMsg }
}

# 2. Nennt der Befehl .claude oder liegt das Arbeitsverzeichnis darin, duerfen nur lesende Programme laufen
if (-not ((Test-Trigger $norm) -or (Test-Trigger $norm2) -or (Test-Trigger $cwdNorm))) { exit 0 }

$t = $norm -replace '\d?>&\d', '' -replace '\d?>\s*(\$null|/dev/null|nul)\b', ''
if ($t -match '>') { Stop-Blocked $msg }

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
    # Programme, die ueber eine Option selbst Befehle ausfuehren
    if ($p -eq 'rg') {
      foreach ($x in @($inv.Arguments)) { if ($x.ToLower().StartsWith('--pre')) { $ok = $false } }
    }
  } elseif ($p -eq 'git') {
    $sub = Get-GitSub @($inv.Arguments) ''
    if (-not $sub) {
      $ok = $true
    } elseif (($gitReadOnly -contains $sub.Name) -and ($sub.Config.Count -eq 0)) {
      $ok = $true
      foreach ($x in @($inv.Arguments)) {
        if ($x.ToLower().StartsWith('--output') -or ($x -cmatch '^-O') -or ($x -ceq '--open-files-in-pager')) { $ok = $false }
      }
    }
  } elseif ($p -match '^\$_(\.(name|fullname|extension|basename|length|directory|lastwritetime|creationtime|psiscontainer|parent))?$') {
    # $_ nur zum Lesen harmloser Eigenschaften, nicht fuer Methoden wie .Delete()
    $ok = $true
  }
  if (-not $ok) { Stop-Blocked $msg }
}
exit 0