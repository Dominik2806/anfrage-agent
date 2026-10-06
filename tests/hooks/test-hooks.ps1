# Testet alle drei Hooks direkt, ohne Claude Code.
# Aufruf: powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/hooks/test-hooks.ps1

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
Gap "Bash grep -r KEY . (durchsucht auch .env)" $h (BashCall "grep -r KEY ." $proj)

$py = "python -c " + [char]34 + "open('.'+'env')" + [char]34
Check "Bash cat .e* (Platzhalter)" $h (BashCall "cat .e*" $proj) 2
Check "Bash cat .en? (Platzhalter)" $h (BashCall "cat .en?" $proj) 2
Check "Bash cat .[e]nv (Zeichenklasse)" $h (BashCall "cat .[e]nv" $proj) 2
Check "Bash cat .* (alle Punktdateien)" $h (BashCall "cat .*" $proj) 2
Check "Bash Get-Content .e* (PowerShell)" $h (BashCall "Get-Content .e*" $proj) 2
Check "Bash Get-Content ./.e* (mit Pfad)" $h (BashCall "Get-Content ./.e*" $proj) 2
Check 'Bash gc (".en"+"v") (zusammengesetzter Name)' $h (BashCall 'gc (".en"+"v")' $proj) 2
Check "Bash cat '.e'nv (Verkettung in Bash)" $h (BashCall "cat '.e'nv" $proj) 2
Check 'Bash type .e""nv (Anfuehrungszeichen im Namen)' $h (BashCall 'type .e""nv' $proj) 2
Check "python -c open('.'+'env')" $h (BashCall $py $proj) 2
Check "Bash cat README.md (erlaubt)" $h (BashCall "cat README.md" $proj) 0
Check "Bash git log --oneline -3 (erlaubt)" $h (BashCall "git log --oneline -3" $proj) 0
Check "Bash grep -E a.*b datei (erlaubt)" $h (BashCall 'grep -E "a.*b" datei.txt' $proj) 0
Check "Bash cat .eslintrc.json (erlaubt)" $h (BashCall "cat .eslintrc.json" $proj) 0
Check "Bash ls .github (erlaubt)" $h (BashCall "ls .github" $proj) 0
Check "Commit-Text mit .* (bekannte Falsch-Blockade)" $h (BashCall 'git commit -m "fix .* handling"' $proj) 2
Gap 'Bash Get-ChildItem -Force | % {gc $_} (liest alle Dateien)' $h (BashCall 'Get-ChildItem -Force | % {gc $_}' $proj)

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
Check "git -c x=y push origin HEAD:main" $h (BashCall "git -c core.x=y push origin HEAD:main" $repoFeat) 2
Check "git -C . -c x=y push origin main" $h (BashCall "git -C . -c core.x=y push origin main" $repoFeat) 2
Check "git 'push' origin main (Anfuehrungszeichen)" $h (BashCall "git 'push' origin main" $repoFeat) 2
Check "git --git-dir .git push origin main" $h (BashCall "git --git-dir .git push origin main" $repoFeat) 2
Check "git push --all" $h (BashCall "git push --all" $repoFeat) 2
Check "git push --mirror" $h (BashCall "git push --mirror" $repoFeat) 2
Check "PowerShell: & git push origin main" $h (BashCall "& git push origin main" $repoFeat) 2
Check "voller Pfad zu git.exe" $h (BashCall '& "C:\Program Files\Git\cmd\git.exe" push origin main' $repoFeat) 2
Check "git -c x=y push -u origin feature/F02-hooks (erlaubt)" $h (BashCall "git -c core.x=y push -u origin feature/F02-hooks" $repoFeat) 0
Check "git -c x=y status (erlaubt)" $h (BashCall "git -c core.x=y status" $repoFeat) 0
Check "git -c user.name=x commit -m push (erlaubt)" $h (BashCall 'git -c user.name=x commit -m push' $repoFeat) 0
"--- Testrepository auf main ---"
Check "push ohne Ziel auf main" $h (BashCall "git push" $repoMain) 2
Check "git status auf main (erlaubt)" $h (BashCall "git status" $repoMain) 0
Check "unlesbare Eingabe wird blockiert" $h "das ist kein json" 2

"=== block-gh-merge.ps1 ==="
$h = "block-gh-merge.ps1"
Check "gh pr merge 3 --squash" $h (BashCall "gh pr merge 3 --squash" $proj) 2
Check "gh pr merge 3 --auto" $h (BashCall "gh pr merge 3 --auto" $proj) 2
Check "gh pr merge ohne Nummer" $h (BashCall "gh pr merge" $proj) 2
Check "gh pr -R owner/repo merge 3" $h (BashCall "gh pr -R owner/repo merge 3" $proj) 2
Check "Umgebungsvariable vor gh pr merge" $h (BashCall "GH_REPO=o/r gh pr merge 3" $proj) 2
Check "PowerShell: & gh pr merge 3" $h (BashCall "& gh pr merge 3" $proj) 2
Check "voller Pfad zu gh.exe" $h (BashCall '& "C:\Program Files\GitHub CLI\gh.exe" pr merge 3' $proj) 2
Check "git status && gh pr merge 3" $h (BashCall "git status && gh pr merge 3" $proj) 2
Check "gh api -X PUT .../merge" $h (BashCall "gh api -X PUT repos/o/r/pulls/3/merge" $proj) 2
Check "gh api ... --method PUT" $h (BashCall "gh api repos/o/r/pulls/3/merge --method PUT" $proj) 2
Check "gh api graphql mit mergePullRequest" $h (BashCall "gh api graphql -f query=mutation{mergePullRequest}" $proj) 2
Check "gh api -X DELETE rulesets" $h (BashCall "gh api -X DELETE repos/o/r/rulesets/1" $proj) 2
Check "gh api --method=PATCH" $h (BashCall "gh api --method=PATCH repos/o/r" $proj) 2
Check "gh alias set" $h (BashCall "gh alias set m 'pr merge'" $proj) 2
Check "unlesbare Eingabe wird blockiert" $h "das ist kein json" 2
Check "gh pr create --fill (erlaubt)" $h (BashCall "gh pr create --fill" $proj) 0
Check "gh pr create mit Titel 'merge fix' (erlaubt)" $h (BashCall 'gh pr create --title "merge fix" --body-file x.md' $proj) 0
Check "gh pr view 3 (erlaubt)" $h (BashCall "gh pr view 3" $proj) 0
Check "gh pr diff (erlaubt)" $h (BashCall "gh pr diff" $proj) 0
Check "gh api GET (erlaubt)" $h (BashCall "gh api repos/o/r/pulls/3" $proj) 0
Check "gh api GET mit --jq (erlaubt)" $h (BashCall "gh api repos/o/r/pulls/3 --jq .title" $proj) 0
Check "gh api -X GET mit -f (erlaubt)" $h (BashCall "gh api -X GET repos/o/r/pulls -f state=open" $proj) 0
Check "gh issue create (erlaubt)" $h (BashCall "gh issue create --title x --body-file y.md" $proj) 0
Check "gh auth status (erlaubt)" $h (BashCall "gh auth status" $proj) 0
Check "echo gh pr merge (erlaubt, nur Text)" $h (BashCall "echo gh pr merge" $proj) 0
Check "git commit mit Text 'gh pr merge' (erlaubt)" $h (BashCall 'git commit -m "gh pr merge"' $proj) 0

"=== block-guard-paths.ps1 ==="
$h = "block-guard-paths.ps1"
$pyw = "python -c " + [char]34 + "open('.claude/hooks/block-env.ps1','w')" + [char]34
Check "Set-Content auf Hook-Skript" $h (BashCall "Set-Content .claude/hooks/block-env.ps1 'exit 0'" $proj) 2
Check "echo exit 0 > Hook-Skript" $h (BashCall "echo exit 0 > .claude/hooks/block-env.ps1" $proj) 2
Check "Set-Content mit Backslashes" $h (BashCall "Set-Content .claude\hooks\block-env.ps1 x" $proj) 2
Check "Add-Content auf settings.json" $h (BashCall "Add-Content .claude/settings.json x" $proj) 2
Check "Remove-Item settings.json" $h (BashCall "Remove-Item -Force .claude\settings.json" $proj) 2
Check "rm Hook-Skript" $h (BashCall "rm .claude/hooks/block-env.ps1" $proj) 2
Check "git checkout auf Hook-Skript" $h (BashCall "git checkout -- .claude/hooks/block-env.ps1" $proj) 2
Check "sed -i auf Hook-Skript" $h (BashCall "sed -i s/2/0/ .claude/hooks/block-main-push.ps1" $proj) 2
Check "python open mit w" $h (BashCall $pyw $proj) 2
Check "WriteAllText auf settings.json" $h (BashCall "[IO.File]::WriteAllText('.claude/settings.json','{}')" $proj) 2
Check "Set-Content settings.local.json" $h (BashCall "Set-Content .claude/settings.local.json x" $proj) 2
Check "cd in Hook-Ordner und Umleitung" $h (BashCall "cd .claude/hooks && echo x > a.ps1" $proj) 2
Check "zusammengesetzter Pfad" $h (BashCall 'Set-Content (".claude/ho"+"oks/block-env.ps1") x' $proj) 2
Check "unlesbare Eingabe wird blockiert" $h "das ist kein json" 2
Check "cat Hook-Skript (erlaubt)" $h (BashCall "cat .claude/hooks/block-env.ps1" $proj) 0
Check "Get-Content settings.json (erlaubt)" $h (BashCall "Get-Content .claude/settings.json" $proj) 0
Check "ls Hook-Ordner (erlaubt)" $h (BashCall "ls .claude/hooks" $proj) 0
Check "git diff Hook-Skript (erlaubt)" $h (BashCall "git diff .claude/hooks/block-env.ps1" $proj) 0
Check "git add Hook-Skript (erlaubt)" $h (BashCall "git add .claude/hooks/block-env.ps1" $proj) 0
Check "git add block-gh-merge.ps1 (erlaubt)" $h (BashCall "git add .claude/hooks/block-gh-merge.ps1" $proj) 0
Check "cat settings.json mit 2>&1 (erlaubt)" $h (BashCall "cat .claude/settings.json 2>&1" $proj) 0
Check "Set-Content in docs (erlaubt)" $h (BashCall "Set-Content docs/notes.md x" $proj) 0
Check "Umleitung in tests (erlaubt)" $h (BashCall "echo x > tests/hooks/out.txt" $proj) 0
Check "git status (erlaubt)" $h (BashCall "git status" $proj) 0
Gap "cd .claude; cd hooks; echo x > a.ps1 (Pfad ueber mehrere Befehle)" $h (BashCall "cd .claude; cd hooks; echo x > a.ps1" $proj)

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

"=== settings.json ==="
function CheckConfig($label, $ok) {
  $script:total++
  if ($ok) { $r = "OK    " } else { $r = "FEHLER"; $script:fail++ }
  "{0} {1}" -f $r, $label
}
$settingsPath = Join-Path $proj ".claude\settings.json"
$cfg = $null
try { $cfg = [System.IO.File]::ReadAllText($settingsPath) | ConvertFrom-Json } catch { }
CheckConfig "settings.json ist gueltiges JSON" ($null -ne $cfg)
if ($cfg) {
  $files = @()
  foreach ($event in $cfg.hooks.PSObject.Properties) {
    foreach ($entry in $event.Value) {
      foreach ($hk in $entry.hooks) {
        foreach ($m in [regex]::Matches([string]$hk.command, '\\hooks\\([A-Za-z0-9._-]+\.ps1)')) { $files += $m.Groups[1].Value }
      }
    }
  }
  $files = @($files | Sort-Object -Unique)
  CheckConfig "settings.json nennt mindestens drei Hook-Skripte" ($files.Count -ge 3)
  foreach ($name in $files) {
    $rel = ".claude/hooks/" + $name
    CheckConfig ("Hook-Skript vorhanden: " + $name) (Test-Path (Join-Path $proj $rel))
    & git -C $proj ls-files --error-unmatch $rel 2>$null | Out-Null
    CheckConfig ("Hook-Skript von Git verfolgt: " + $name) ($LASTEXITCODE -eq 0)
  }
  CheckConfig "Edit-Sperre fuer .claude/hooks vorhanden" (@($cfg.permissions.deny) -contains "Edit(/.claude/hooks/**)")
  CheckConfig "Edit-Sperre fuer settings.json vorhanden" (@($cfg.permissions.deny) -contains "Edit(/.claude/settings.json)")
  CheckConfig "Rueckfrage vor git push vorhanden" (@($cfg.permissions.ask) -contains "Bash(git push *)")
}

""
"Ergebnis: {0} Tests, {1} Fehler" -f $script:total, $script:fail
exit $script:fail
