# Testet alle drei Hooks direkt, ohne Claude Code.
# Aufruf: powershell.exe -NoProfile -ExecutionPolicy Bypass -File .claude\hooks\test-hooks.ps1

$proj = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$hooks = Join-Path $proj ".claude\hooks"
$utf8 = New-Object System.Text.UTF8Encoding($false)
$script:total = 0
$script:fail = 0

function Invoke-Hook($name, $json) {
  $json | powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $hooks $name) 2>$null | Out-Null
  return $LASTEXITCODE
}

function ToJson($payload) {
  if ($payload -is [string]) { return $payload }
  return ($payload | ConvertTo-Json -Compress -Depth 5)
}

function Check($label, $hook, $payload, $expected) {
  $script:total++
  $code = Invoke-Hook $hook (ToJson $payload)
  if ($code -eq $expected) { $r = "OK    " } else { $r = "FEHLER"; $script:fail++ }
  "{0} {1} (erwartet {2}, erhalten {3})" -f $r, $label, $expected, $code
}

function Gap($label, $hook, $payload) {
  $code = Invoke-Hook $hook (ToJson $payload)
  if ($code -eq 0) { "LUECKE {0} (bekannt, der Hook laesst es durch)" -f $label }
  else { "BESSER {0} (wird jetzt blockiert)" -f $label }
}

function ReadCall($path) { @{ tool_name = "Read"; tool_input = @{ file_path = $path } } }
function BashCall($cmd, $dir) { @{ tool_name = "Bash"; cwd = $dir; tool_input = @{ command = $cmd } } }

"=== block-env.ps1 ==="
$h = "block-env.ps1"
Check "Read .env" $h (ReadCall "C:\proj\.env") 2
Check "Read .env.local" $h (ReadCall "C:\proj\.env.local") 2
Check "Read .ENV (Grossbuchstaben)" $h (ReadCall "C:\proj\.ENV") 2
Check "Read .env.production" $h (ReadCall "C:\proj\.env.production") 2
Check "Read Pfad mit .." $h (ReadCall "C:\proj\sub\..\.env") 2
Check "Read .env.example (erlaubt)" $h (ReadCall "C:\proj\.env.example") 0
Check "Read README.md (erlaubt)" $h (ReadCall "C:\proj\README.md") 0
Check "Bash cat .env" $h (BashCall "cat .env" $proj) 2
Check "Bash type .env" $h (BashCall "type .env" $proj) 2
Check "Bash Get-Content .env" $h (BashCall "Get-Content .env" $proj) 2
Check "Bash cat '.env' in Anfuehrungszeichen" $h (BashCall "cat '.env'" $proj) 2
Check "Bash cat .env.example (erlaubt)" $h (BashCall "cat .env.example" $proj) 0
Check "Bash git status (erlaubt)" $h (BashCall "git status" $proj) 0
Check "Grep mit glob .env*" $h @{ tool_name = "Grep"; tool_input = @{ pattern = "KEY"; glob = ".env*" } } 2
Check "Write mit Text '.env' im Inhalt (erlaubt)" $h @{ tool_name = "Write"; tool_input = @{ file_path = "docs\x.md"; content = "Siehe .env" } } 0
Check "unlesbare Eingabe wird blockiert" $h "das ist kein json" 2
Check "Commit-Text mit .env (bekannte Falsch-Blockade)" $h (BashCall 'git commit -m "ignore .env"' $proj) 2
Gap "Bash cat .e* (Platzhalter)" $h (BashCall "cat .e*" $proj)
Gap "Bash grep -r KEY . (durchsucht auch .env)" $h (BashCall "grep -r KEY ." $proj)
Gap "Bash Get-Content .e* (Platzhalter, PowerShell)" $h (BashCall "Get-Content .e*" $proj)
Gap 'Bash gc (".en"+"v") (zusammengesetzter Name)' $h (BashCall 'gc (".en"+"v")' $proj)

"=== block-main-push.ps1 ==="
$h = "block-main-push.ps1"
$tmp = Join-Path $env:TEMP ("hooktest-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $tmp | Out-Null
$repoMain = Join-Path $tmp "repo-main"
$repoFeat = Join-Path $tmp "repo-feature"
git init -q -b main $repoMain
git init -q -b feature/test $repoFeat

"--- Testrepository auf Feature-Branch ---"
Check "push origin main" $h (BashCall "git push origin main" $repoFeat) 2
Check "push origin master" $h (BashCall "git push origin master" $repoFeat) 2
Check "push origin HEAD:main" $h (BashCall "git push origin HEAD:main" $repoFeat) 2
Check "push --force origin main" $h (BashCall "git push --force origin main" $repoFeat) 2
Check "git -C . push origin main" $h (BashCall "git -C . push origin main" $repoFeat) 2
Check "echo | git push origin main" $h (BashCall "echo hallo | git push origin main" $repoFeat) 2
Check "push origin main; echo" $h (BashCall "git push origin main; echo fertig" $repoFeat) 2
Check "push -u origin feature/F02-hooks (erlaubt)" $h (BashCall "git push -u origin feature/F02-hooks" $repoFeat) 0
Check "push ohne Ziel auf Feature-Branch (erlaubt)" $h (BashCall "git push" $repoFeat) 0
Check "push origin feature/main-fix (erlaubt)" $h (BashCall "git push origin feature/main-fix" $repoFeat) 0
Check "push && echo main (erlaubt)" $h (BashCall "git push && echo main" $repoFeat) 0
Check "pull origin main (erlaubt)" $h (BashCall "git pull origin main" $repoFeat) 0
Check "git status (erlaubt)" $h (BashCall "git status" $repoFeat) 0
Check "commit mit Text 'push' (erlaubt)" $h (BashCall 'git commit -m "docs: push"' $repoFeat) 0
Check "Branch feature/main (bekannte Falsch-Blockade)" $h (BashCall "git push origin feature/main" $repoFeat) 2
Gap "git -c core.x=y push origin HEAD:main (Option vor push)" $h (BashCall "git -c core.x=y push origin HEAD:main" $repoFeat)
Gap "gh pr merge 3 --squash (Merge ueber GitHub-CLI)" $h (BashCall "gh pr merge 3 --squash" $repoFeat)
"--- Testrepository auf main ---"
Check "push ohne Ziel auf main" $h (BashCall "git push" $repoMain) 2
Check "git status auf main (erlaubt)" $h (BashCall "git status" $repoMain) 0
Check "unlesbare Eingabe wird blockiert" $h "das ist kein json" 2

"=== format-file.ps1 ==="
function CheckFormat($label, $file, $content, $expectedExit, $expectedContent) {
  $script:total++
  [System.IO.File]::WriteAllText($file, $content, $utf8)
  $code = Invoke-Hook "format-file.ps1" (ToJson @{ tool_name = "Write"; cwd = $proj; tool_input = @{ file_path = $file } })
  $actual = [System.IO.File]::ReadAllText($file).TrimEnd()
  $same = ($actual -eq $expectedContent)
  if ($code -eq $expectedExit -and $same) { $r = "OK    " } else { $r = "FEHLER"; $script:fail++ }
  $txt = if ($same) { "wie erwartet" } else { "ABWEICHEND: " + $actual }
  "{0} {1} (Exit {2}, Inhalt {3})" -f $r, $label, $code, $txt
}

$f1 = Join-Path $proj "hook-test-tmp.ts"
$f2 = Join-Path $proj "hook-test-tmp.py"
$f3 = Join-Path $proj "hook-test-tmp.md"
$f4 = Join-Path $proj "hook-test-broken.ts"
$outside = Join-Path $tmp "outside.ts"
$nm = Join-Path $proj "node_modules\hook-test-tmp.ts"
try {
  CheckFormat "ts wird formatiert" $f1 "const   x = {a:1,   b:[1,2,3]}" 0 "const x = { a: 1, b: [1, 2, 3] };"
  CheckFormat "py wird formatiert" $f2 "x   = {  'a':1 }" 0 'x = {"a": 1}'
  CheckFormat "md bleibt unveraendert" $f3 "|a|b|`n|-|-|" 0 "|a|b|`n|-|-|"
  CheckFormat "Syntaxfehler wird gemeldet" $f4 "const x = {" 2 "const x = {"
  CheckFormat "Datei ausserhalb des Projekts bleibt unveraendert" $outside "const   x = 1" 0 "const   x = 1"
  CheckFormat "node_modules bleibt unveraendert" $nm "const   x = 1" 0 "const   x = 1"
  Check "nicht vorhandene Datei" "format-file.ps1" @{ tool_name = "Write"; cwd = $proj; tool_input = @{ file_path = (Join-Path $proj "gibt-es-nicht.ts") } } 0
} finally {
  Remove-Item $f1, $f2, $f3, $f4, $nm -ErrorAction SilentlyContinue
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

""
"Ergebnis: {0} Tests, {1} Fehler" -f $script:total, $script:fail
exit $script:fail