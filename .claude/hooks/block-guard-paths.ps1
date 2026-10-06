# Schuetzt Hooks und Einstellungen vor Aenderungen per Shell (PreToolUse-Hook fuer Bash und PowerShell).
# Exit 2 = Befehl blockieren, Exit 0 = erlauben.

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

# Anfuehrungszeichen, Gravis, ^ und + entfernen, Backslashes angleichen
$norm = (($cmd -replace '["''`^+]', '') -replace '\\', '/').ToLower()

$protected = '\.claude/(hooks|settings)|settings\.local\.json'
if ($norm -notmatch $protected) { exit 0 }

# Umleitungen auf null und Stream-Verkettungen sind keine Schreibzugriffe
$t = $norm -replace '\d?>&\d', '' -replace '\d?>\s*(\$null|/dev/null|nul)\b', ''

$writeVerbs = '\b(set-content|add-content|out-file|outfile|new-item|remove-item|clear-content|move-item|copy-item|rename-item|set-itemproperty|set-acl|tee-object|tee|rm|del|erase|rd|ren|mv|cp|sed|truncate|attrib|icacls|takeown|touch|ln|robocopy|xcopy)\b'
$gitWrite = '\bgit\s+(?:(?:-[a-z]\s+\S+|--?\S+)\s+)*(checkout|restore|reset|clean|rm|mv|stash|apply)\b'
$dotnetWrite = 'writealltext|writeallbytes|writealllines|appendall|streamwriter|filestream|\bopen\s*\('

if (($t -match $writeVerbs) -or ($t -match $gitWrite) -or ($t -match $dotnetWrite) -or ($t -match '>')) {
  [Console]::Error.WriteLine($msg)
  exit 2
}
exit 0