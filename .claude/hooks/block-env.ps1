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
$tool = [string]$data.tool_name
$msg = "Blockiert: Zugriff auf .env-Dateien ist in diesem Projekt nicht erlaubt. Nutze stattdessen .env.example."

# Felder mit Pfaden und Befehlen. Bei Glob ist pattern ein Dateimuster.
$names = @("file_path", "path", "command", "glob")
if ($tool -eq "Glob") { $names += "pattern" }
$values = @()
foreach ($name in $names) {
  if ($ti -and $ti.$name) { $values += [string]$ti.$name }
}
# Bei Grep ist pattern ein Suchtext: nur auf das Literal pruefen
$searchText = @()
if ($ti -and $ti.pattern -and $tool -ne "Glob") { $searchText += [string]$ti.pattern }

$envPattern = '(^|[\s\\/"''=(,{\[])\.env(\.(?!example\b)[\w.-]+)?($|[\s\\/"''|;&)*?,}\]])'

function Test-EnvGlob([string]$text) {
  foreach ($tok in ($text -split '[\s\\/"''=,;|&(){}]+')) {
    if ($tok -match '^\.(e|en|env)?[*?\[]') { return $true }
  }
  return $false
}

foreach ($v in $values) {
  # Fassung ohne Anfuehrungszeichen, Gravis, ^ und +, damit zusammengesetzte Namen auffallen
  $norm = $v -replace '["''`^+]', ''
  if (($v -match $envPattern) -or ($norm -match $envPattern) -or (Test-EnvGlob $v) -or (Test-EnvGlob $norm)) {
    [Console]::Error.WriteLine($msg)
    exit 2
  }
}
foreach ($v in $searchText) {
  if ($v -match $envPattern) {
    [Console]::Error.WriteLine($msg)
    exit 2
  }
}
exit 0