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

# Felder mit Pfaden und Befehlen. Bei Glob ist pattern ein Dateimuster, bei Grep ein Suchtext (nicht geprueft).
$names = @("file_path", "notebook_path", "path", "command", "glob")
if ($tool -eq "Glob") { $names += "pattern" }
$values = @()
foreach ($name in $names) {
  if ($ti -and $ti.$name) { $values += [string]$ti.$name }
}

$candidates = @('.env', '.env.local', '.env.production', '.env.development', '.env.test', '.env.staging')

function Test-EnvName([string]$word) {
  # Nur den letzten Pfadteil betrachten
  $name = $word -replace '\\', '/'
  $name = $name.Substring($name.LastIndexOf('/') + 1)
  # Windows ignoriert Punkte und Leerzeichen am Ende; alternative Datenstroeme (::$DATA) entfernen
  $name = ($name -replace ':.*$', '').TrimEnd('.', ' ').ToLower()
  if ($name.Length -eq 0) { return $false }

  # .env und alles, was mit .env. beginnt; nur .env.example ist erlaubt
  if (($name -match '^\.env(\..+)?$') -and ($name -ne '.env.example')) { return $true }

  # Platzhalter: blockieren, wenn er eine .env-Datei treffen kann
  if ($name -match '[*?\[]') {
    if ((-not $name.StartsWith('.')) -and ($name -notmatch '[env]')) { return $false }
    try {
      foreach ($c in $candidates) { if ($c -like $name) { return $true } }
    } catch { return $true }
  }
  return $false
}

foreach ($v in $values) {
  # Anfuehrungszeichen, Gravis, ^ und + entfernen, damit zusammengesetzte Namen auffallen
  $clean = $v -replace '["''`^+]', ''
  foreach ($w in ($clean -split '[\s=,;|&(){}<>]+')) {
    if ($w -and (Test-EnvName $w)) {
      [Console]::Error.WriteLine($msg)
      exit 2
    }
  }
}
exit 0