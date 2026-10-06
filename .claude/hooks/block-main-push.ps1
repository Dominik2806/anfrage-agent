# Blockiert Pushes auf main/master (PreToolUse-Hook fuer Bash und PowerShell).
# Exit 2 = Befehl blockieren, Exit 0 = erlauben.

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

function Get-Tokens([string]$segment) {
  $found = [regex]::Matches($segment, '"[^"]*"|''[^'']*''|\S+')
  $result = @()
  foreach ($f in $found) { $result += $f.Value.Trim([char]34, [char]39) }
  return $result
}

# Ein Befehl kann aus mehreren Teilen bestehen: ; && || | oder Zeilenumbruch.
$segments = $cmd -split '&&|\|\||[;|&\r\n]'

foreach ($segment in $segments) {
  $tokens = @(Get-Tokens $segment)
  $i = 0
  while ($i -lt $tokens.Count -and $tokens[$i] -match '^(&|\.|call|[A-Za-z_][A-Za-z0-9_]*=.*)$') { $i++ }
  if ($i -ge $tokens.Count) { continue }

  $leaf = ($tokens[$i] -replace '^.*[\\/]', '').ToLower() -replace '\.exe$', ''
  if ($leaf -ne 'git') { continue }
  $i++

  # Globale git-Optionen samt Wert ueberspringen (-c name=wert, -C Pfad, ...)
  $repo = $cwd
  while ($i -lt $tokens.Count -and $tokens[$i].StartsWith('-')) {
    $opt = $tokens[$i]
    if ($opt -ceq '-C' -and ($i + 1) -lt $tokens.Count) {
      try {
        $p = $tokens[$i + 1]
        if ([System.IO.Path]::IsPathRooted($p)) { $repo = $p } else { $repo = Join-Path $repo $p }
      } catch { }
      $i += 2
      continue
    }
    if ($opt -cin @('-c', '--git-dir', '--work-tree', '--namespace', '--config-env') -and ($i + 1) -lt $tokens.Count) {
      $i += 2
      continue
    }
    $i++
  }
  if ($i -ge $tokens.Count) { continue }
  if ($tokens[$i].ToLower() -ne 'push') { continue }

  # Ziel pruefen: main, master, HEAD:main, +main, refs/heads/main, --all, --mirror
  $pushArgs = @($tokens | Select-Object -Skip ($i + 1))
  foreach ($a in $pushArgs) {
    if ($a -match '^(--all|--mirror)$' -or $a -match '(^|[:/+])(main|master)$') {
      [Console]::Error.WriteLine($msg)
      exit 2
    }
  }

  # Ohne Ziel gilt der aktuelle Branch
  $branch = ''
  try { $branch = (& git -C $repo branch --show-current 2>$null) } catch { $branch = '' }
  if ($branch -match '^(main|master)$') {
    [Console]::Error.WriteLine($msg)
    exit 2
  }
}
exit 0