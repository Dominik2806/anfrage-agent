# Formatiert geaenderte Dateien (PostToolUse-Hook fuer Edit/Write).
# Exit 0 = fertig oder nicht zustaendig. Exit 2 = Formatierer meldet einen Fehler (Claude sieht ihn).

$raw = [Console]::In.ReadToEnd()
try { $data = $raw | ConvertFrom-Json } catch { exit 0 }

$file = [string]$data.tool_input.file_path
if (-not $file) { exit 0 }

$proj = $env:CLAUDE_PROJECT_DIR
if (-not $proj) {
  $proj = if ($data.cwd) { [string]$data.cwd } else { (Get-Location).Path }
}

if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { exit 0 }
$full = (Resolve-Path -LiteralPath $file).Path
$projFull = (Resolve-Path -LiteralPath $proj).Path.TrimEnd('\') + '\'

# Nur Dateien innerhalb des Projekts, nie node_modules oder .git
if (-not $full.StartsWith($projFull, [StringComparison]::OrdinalIgnoreCase)) { exit 0 }
if ($full -match '[\\/](node_modules|\.git)[\\/]') { exit 0 }

$ext = [System.IO.Path]::GetExtension($full).ToLower()
$prettierExt = @(".ts", ".tsx", ".js", ".jsx", ".json", ".css")

if ($prettierExt -contains $ext) {
  $bin = Join-Path $projFull "node_modules\.bin\prettier.cmd"
  if (-not (Test-Path -LiteralPath $bin)) {
    [Console]::Error.WriteLine("Hook format-file: Prettier ist nicht installiert. Im Projektordner npm install ausfuehren.")
    exit 2
  }
  $out = & $bin --write --log-level warn $full 2>&1
} elseif ($ext -eq ".py") {
  $out = & python -m ruff format --quiet $full 2>&1
} else {
  exit 0
}

if ($LASTEXITCODE -ne 0) {
  [Console]::Error.WriteLine("Formatierung fehlgeschlagen fuer " + $full + ":`n" + ($out | Out-String))
  exit 2
}
exit 0