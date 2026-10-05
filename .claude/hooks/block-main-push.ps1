# Blockiert Pushes auf main/master (PreToolUse-Hook fuer Bash).
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

$pushPattern = '\bgit(\s+-C\s+("[^"]*"|\S+))?(\s+-[-\w=]+)*\s+push\b'
$m = [regex]::Match($cmd, $pushPattern)
if (-not $m.Success) { exit 0 }

$rest = $cmd.Substring($m.Index + $m.Length)
$rest = ($rest -split '[;&|\r\n]')[0]

$msg = "Blockiert: Pushes auf main/master sind nicht erlaubt. Arbeite auf einem Feature-Branch und erstelle einen Pull Request."

if ($rest -match '(^|[\s:/+])(main|master)($|\s)') {
  [Console]::Error.WriteLine($msg)
  exit 2
}

$dir = if ($data.cwd) { [string]$data.cwd } else { $env:CLAUDE_PROJECT_DIR }
$branch = (& git -C $dir branch --show-current 2>$null)
if ($branch -match '^(main|master)$') {
  [Console]::Error.WriteLine($msg)
  exit 2
}
exit 0