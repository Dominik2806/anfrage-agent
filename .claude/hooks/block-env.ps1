# Blockiert Zugriffe auf .env-Dateien (PreToolUse-Hook).
# Exit 2 = Werkzeugaufruf blockieren, Exit 0 = erlauben.

. (Join-Path $PSScriptRoot '_common.ps1')

$raw = [Console]::In.ReadToEnd()
try {
  $data = $raw | ConvertFrom-Json
} catch {
  [Console]::Error.WriteLine("Hook block-env: Eingabe nicht lesbar. Aufruf blockiert.")
  exit 2
}

$ti = $data.tool_input
$tool = [string]$data.tool_name
# Glob und Grep listen nur Dateinamen oder suchen; ein reiner Platzhalter (*, **/*) ist dort harmlos
$listingTool = ($tool -eq "Glob") -or ($tool -eq "Grep")
$msg = "Blockiert: Zugriff auf .env-Dateien ist in diesem Projekt nicht erlaubt. Nutze stattdessen .env.example."

function Stop-Blocked {
  [Console]::Error.WriteLine($msg)
  exit 2
}

# Felder mit Pfaden. Bei Glob ist pattern ein Dateimuster, bei Grep ein Suchtext (nicht geprueft).
$names = @("file_path", "notebook_path", "path", "glob")
if ($tool -eq "Glob") { $names += "pattern" }

$candidates = @('.env', '.env.local', '.env.production', '.env.development', '.env.test', '.env.staging',
  '.env.dev', '.env.prod', '.env.ci', '.env.qa', '.env.stage', '.env.docker', '.env.default')

# Programme, bei denen ein reiner Platzhalter (*, *.*, ????) nur auflistet oder nichts liest
$listing = @('ls', 'dir', 'gci', 'get-childitem', 'tree', 'echo', 'write-output', 'write-host', 'git', 'mkdir', 'md',
  'cd', 'chdir', 'set-location', 'sl', 'pwd', 'test-path')

function Test-EnvName([string]$word, [bool]$isListing) {
  # Nur den letzten Pfadteil betrachten
  $name = $word -replace '\\', '/'
  $name = $name.Substring($name.LastIndexOf('/') + 1)
  # Parameter mit Doppelpunkt: -Path:.env
  if ($name -match '^-[A-Za-z]+:(.+)$') {
    $name = $Matches[1]
    $name = $name.Substring($name.LastIndexOf('/') + 1)
  }
  # Laufwerk ohne Pfad (C:.env), alternative Datenstroeme (::$DATA), Punkte und Leerzeichen am Ende
  $name = $name -replace '^[A-Za-z]:(?!:)', ''
  $name = ($name -replace ':.*$', '').TrimEnd('.', ' ').ToLower()
  if ($name.Length -eq 0) { return $false }

  # .env und alles, was mit .env. beginnt; nur .env.example ist erlaubt
  if (($name -match '^\.env(\..+)?$') -and ($name -ne '.env.example')) { return $true }
  # Windows-Kurzname (8.3)
  if ($name -match '^env~\d') { return $true }

  # Platzhalter: blockieren, wenn er eine .env-Datei treffen kann
  if ($name -match '[*?\[]') {
    if ($isListing -and ($name -match '^[*?.]+$')) { return $false }
    try {
      foreach ($c in $candidates) { if ($c -like $name) { return $true } }
    } catch { return $true }
  }
  return $false
}

function Test-Words([string]$text, [bool]$isListing) {
  foreach ($w in ($text -split '[\s=,;|&(){}<>]+')) {
    if ($w -and (Test-EnvName $w $isListing)) { return $true }
  }
  return $false
}

# Anfuehrungszeichen, Gravis, ^ und + entfernen, damit zusammengesetzte Namen auffallen.
# Die zweite Fassung entfernt auch Backslashes (Bash-Escapes wie .en\v).
function Get-Variants([string]$v) {
  $c1 = $v -replace '["''`^+]', ''
  $c2 = $c1 -replace '\\', ''
  return @($c1, $c2)
}

foreach ($name in $names) {
  if ($ti -and $ti.$name) {
    foreach ($variant in (Get-Variants ([string]$ti.$name))) {
      if (Test-Words $variant $listingTool) { Stop-Blocked }
    }
  }
}

if ($ti -and $ti.command) {
  foreach ($variant in (Get-Variants ([string]$ti.command))) {
    foreach ($inv in @(Get-Invocations $variant)) {
      if ($inv.Program.StartsWith('<')) { Stop-Blocked }
      $isListing = $listing -contains $inv.Program
      if (Test-Words $inv.Program $true) { Stop-Blocked }
      foreach ($arg in @($inv.Arguments)) {
        if (Test-Words ([string]$arg) $isListing) { Stop-Blocked }
      }
    }
  }
}
exit 0