# Blockiert Zugriffe auf .env-Dateien (PreToolUse-Hook).
# Exit 2 = Werkzeugaufruf blockieren, Exit 0 = erlauben.

$raw = [Console]::In.ReadToEnd()

try {
  $data = $raw | ConvertFrom-Json
} catch {
  [Console]::Error.WriteLine("Hook block-env: Eingabe nicht lesbar. Aufruf blockiert.")
  exit 2
}

$ti = $data.tool_input
$values = @()
foreach ($name in "file_path", "path", "command", "pattern", "glob") {
  if ($ti -and $ti.$name) { $values += [string]$ti.$name }
}

$envPattern = '(^|[\s\\/"''=])\.env(\.(?!example\b)[\w.-]+)?($|[\s\\/"''|;&)*?])'

foreach ($v in $values) {
  if ($v -match $envPattern) {
    [Console]::Error.WriteLine("Blockiert: Zugriff auf .env-Dateien ist in diesem Projekt nicht erlaubt. Nutze stattdessen .env.example.")
    exit 2
  }
}
exit 0