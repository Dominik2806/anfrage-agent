# Testet alle Hook-Skripte direkt, ohne Claude Code.
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
Check "Bash echo mit eckiger Klammer im Text (harmlos)" $h (BashCall 'echo "[KLÄREN: Test]"' $proj) 0
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
Check "Commit-Text mit .* (jetzt erlaubt)" $h (BashCall 'git commit -m "fix .* handling"' $proj) 0
Gap 'Bash Get-ChildItem -Force | % {gc $_} (liest alle Dateien)' $h (BashCall 'Get-ChildItem -Force | % {gc $_}' $proj)

Check "Bash cat .env.* (Platzhalter nach der Endung)" $h (BashCall "cat .env.*" $proj) 2
Check "Bash cat .env.? (Platzhalter nach der Endung)" $h (BashCall "cat .env.?" $proj) 2
Check "PowerShell Get-Content *.env (fuehrender Stern)" $h (BashCall "Get-Content *.env" $proj) 2
Check "PowerShell gc ?env (fuehrendes Fragezeichen)" $h (BashCall "gc ?env" $proj) 2
Check "PowerShell gc *env (fuehrender Stern)" $h (BashCall "gc *env" $proj) 2
Check "Windows: Get-Content .env. (Punkt am Ende)" $h (BashCall "Get-Content .env." $proj) 2
Check 'Windows: .env::$DATA (alternativer Datenstrom)' $h (BashCall 'Get-Content .env::$DATA' $proj) 2
Check "Read .env. (Punkt am Ende)" $h (ReadCall "C:\proj\.env.") 2
Check "PowerShell Get-ChildItem *.json (erlaubt)" $h (BashCall "Get-ChildItem *.json" $proj) 0
Check "Bash ls *.md (erlaubt)" $h (BashCall "ls *.md" $proj) 0
Check "PowerShell Get-Content .env.example (erlaubt)" $h (BashCall "Get-Content .env.example" $proj) 0

Check "Bash cat .env.example-prod (nur .env.example ist erlaubt)" $h (BashCall "cat .env.example-prod" $proj) 2
Check "Bash cat .\.env (mit .\)" $h (BashCall 'cat .\.env' $proj) 2
Check "Schluessel ausserhalb des Projekts (erlaubt)" $h (BashCall 'node --env-file=C:\secrets\anfrage-agent.env app.js' $proj) 0
Check "Bash git add . (erlaubt)" $h (BashCall "git add ." $proj) 0
Check "Bash ls * (erlaubt, einzelner Stern)" $h (BashCall "ls *" $proj) 0

Check "NotebookEdit auf .env" $h (@{ tool_name = "NotebookEdit"; tool_input = @{ notebook_path = "C:\proj\.env" } }) 2
Check "MultiEdit auf .env" $h (@{ tool_name = "MultiEdit"; tool_input = @{ file_path = "C:\proj\.env" } }) 2
Check "NotebookEdit auf analyse.ipynb (erlaubt)" $h (@{ tool_name = "NotebookEdit"; tool_input = @{ notebook_path = "C:\proj\analyse.ipynb" } }) 0

$h = "block-env.ps1"
Check 'cat * (Platzhalter allein)' $h (BashCall "cat *" $proj) 2
Check 'Get-Content *.*' $h (BashCall "Get-Content *.*" $proj) 2
Check 'Select-String KEY *.*' $h (BashCall "Select-String KEY *.*" $proj) 2
Check 'gc ????' $h (BashCall "gc ????" $proj) 2
Check 'Get-ChildItem *.* (erlaubt)' $h (BashCall "Get-ChildItem *.*" $proj) 0
Check 'git add * (erlaubt)' $h (BashCall "git add *" $proj) 0
Check 'Get-Content -Path:.env' $h (BashCall "Get-Content -Path:.env" $proj) 2
Check 'Get-Content -LiteralPath:.env' $h (BashCall "Get-Content -LiteralPath:.env" $proj) 2
Check 'Get-Content C:.env (Laufwerk relativ)' $h (BashCall "Get-Content C:.env" $proj) 2
Check 'cat env~1 (Kurzname)' $h (BashCall "cat env~1" $proj) 2
Check 'cat .en\v (Bash-Escape)' $h (BashCall 'cat .en\v' $proj) 2
Check 'gc .e*.dev (nicht gelistete Datei)' $h (BashCall "gc .e*.dev" $proj) 2
Check 'Tiefe: env env env env env cat .env' $h (BashCall "env env env env env cat .env" $proj) 2

$h = "block-env.ps1"
function GlobCall($p) { @{ tool_name = "Glob"; tool_input = @{ pattern = $p } } }
function GrepCall($pt, $path, $glob) { $i = @{ pattern = $pt }; if ($path) { $i.path = $path }; if ($glob) { $i.glob = $glob }; @{ tool_name = "Grep"; tool_input = $i } }
Check "Glob docs/adr/* (erlaubt)" $h (GlobCall "docs/adr/*") 0
Check "Glob **/* (erlaubt)" $h (GlobCall "**/*") 0
Check "Glob * (erlaubt)" $h (GlobCall "*") 0
Check "Glob **/*.md (erlaubt)" $h (GlobCall "**/*.md") 0
Check "Glob .claude/hooks/*.ps1 (erlaubt)" $h (GlobCall ".claude/hooks/*.ps1") 0
Check "Glob .env* (blockiert)" $h (GlobCall ".env*") 2
Check "Glob **/.env (blockiert)" $h (GlobCall "**/.env") 2
Check "Grep path . ohne glob (erlaubt)" $h (GrepCall "TODO" "." $null) 0
Check "Grep glob * (erlaubt)" $h (GrepCall "TODO" "." "*") 0
Check "Grep glob *.md (erlaubt)" $h (GrepCall "TODO" "docs/" "*.md") 0
Check "Grep glob .env* (blockiert)" $h (GrepCall "KEY" "." ".env*") 2
Check "Grep path .env (blockiert)" $h (GrepCall "KEY" ".env" $null) 2
Check "Read docs/adr/README.md (erlaubt)" $h (ReadCall "C:\proj\docs\adr\README.md") 0
Check "Bash ls docs/adr/* (erlaubt)" $h (BashCall "ls docs/adr/*" $proj) 0

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
$h = "block-main-push.ps1"
Check 'Huelle: bash -c "git push origin main"' $h (BashCall 'bash -c "git push origin main"' $repoFeat) 2
Check "Huelle: cmd /c git push origin main" $h (BashCall "cmd /c git push origin main" $repoFeat) 2
Check 'Huelle: powershell -Command "git push origin main"' $h (BashCall 'powershell -Command "git push origin main"' $repoFeat) 2
Check 'Huelle: pwsh -c "git push origin HEAD:main"' $h (BashCall 'pwsh -c "git push origin HEAD:main"' $repoFeat) 2
Check 'Huelle: iex "git push origin main"' $h (BashCall 'iex "git push origin main"' $repoFeat) 2
Check "Klammer: (git push origin main)" $h (BashCall "(git push origin main)" $repoFeat) 2
Check "Block: & { git push origin main }" $h (BashCall "& { git push origin main }" $repoFeat) 2
Check 'Kontrollstruktur: if ($true) { git push origin main }' $h (BashCall 'if ($true) { git push origin main }' $repoFeat) 2
Check 'Unterbefehl: echo $(git push origin main)' $h (BashCall 'echo $(git push origin main)' $repoFeat) 2
Check "Vorsatz: env X=1 git push origin main" $h (BashCall "env X=1 git push origin main" $repoFeat) 2
Check 'Start-Process git -ArgumentList "push origin main"' $h (BashCall 'Start-Process git -ArgumentList "push origin main"' $repoFeat) 2
Check 'Ziel zusammengesetzt: HEAD:ma"in"' $h (BashCall 'git push origin HEAD:ma"in"' $repoFeat) 2
Check 'Huelle: bash -c "git status" (erlaubt)' $h (BashCall 'bash -c "git status"' $repoFeat) 0
Check 'Huelle: powershell -c "git push origin feature/F02-hooks" (erlaubt)' $h (BashCall 'powershell -c "git push origin feature/F02-hooks"' $repoFeat) 0
Check 'Text: git commit -m "docs: push main notes" (erlaubt)' $h (BashCall 'git commit -m "docs: push main notes"' $repoFeat) 0
$h = "block-main-push.ps1"
Check 'Zuweisung: $r = git push origin main' $h (BashCall '$r = git push origin main' $repoFeat) 2
Check 'Zuweisung ohne Leerzeichen' $h (BashCall '$r=git push origin main' $repoFeat) 2
Check 'Tiefe: env env env env env git push' $h (BashCall "env env env env env git push origin main" $repoFeat) 2
Check 'Tiefe: time x5' $h (BashCall "time time time time time git push origin main" $repoFeat) 2
Check 'eval "git push origin main"' $h (BashCall 'eval "git push origin main"' $repoFeat) 2
Check 'timeout 5 git push origin main' $h (BashCall "timeout 5 git push origin main" $repoFeat) 2
Check 'nice git push origin main' $h (BashCall "nice git push origin main" $repoFeat) 2
Check 'saps git -ArgumentList push,origin,main' $h (BashCall "saps git -ArgumentList push,origin,main" $repoFeat) 2
Check "Start-Process git -ArgumentList 'push','origin','main'" $h (BashCall "Start-Process git -ArgumentList 'push','origin','main'" $repoFeat) 2
Check 'powershell -EncodedCommand' $h (BashCall "powershell -EncodedCommand Z2l0IHB1c2g=" $repoFeat) 2
Check 'pwsh -enc' $h (BashCall "pwsh -enc Z2l0IHB1c2g=" $repoFeat) 2
Check 'powershell -e' $h (BashCall "powershell -e Z2l0IHB1c2g=" $repoFeat) 2
Check 'Set-Alias g git; g push origin main' $h (BashCall "Set-Alias g git; g push origin main" $repoFeat) 2
Check 'sal g git' $h (BashCall "sal g git; g push origin main" $repoFeat) 2
Check '$g=git; & $g push origin main' $h (BashCall '$g=''git''; & $g push origin main' $repoFeat) 2
Check 'iex mit Verkettung' $h (BashCall 'iex ("git push" + " origin main")' $repoFeat) 2
Check 'git.cmd push origin main' $h (BashCall "git.cmd push origin main" $repoFeat) 2
Check 'Backtick-Ersetzung: echo `git push origin main`' $h (BashCall 'echo `git push origin main`' $repoFeat) 2
Check 'Ziel mit Backslash: ma\in' $h (BashCall 'git push origin ma\in' $repoFeat) 2
Check 'Ziel aus Variable' $h (BashCall 'git push origin $b' $repoFeat) 2
Check 'Refspec mit Platzhalter' $h (BashCall "git push origin 'refs/heads/*:refs/heads/*'" $repoFeat) 2
Check 'git push --al' $h (BashCall "git push --al" $repoFeat) 2
Check 'git push --mir' $h (BashCall "git push --mir" $repoFeat) 2
Check 'git switch main && git push' $h (BashCall "git switch main && git push" $repoFeat) 2
Check 'git checkout main; git push' $h (BashCall "git checkout main; git push" $repoFeat) 2
Check 'git -c alias.p=!git push p' $h (BashCall "git -c alias.p='!git push origin main' p" $repoFeat) 2
Check 'git switch -c feature/x && git push -u origin feature/x (erlaubt)' $h (BashCall "git switch -c feature/x && git push -u origin feature/x" $repoFeat) 0
Check 'git push -u origin feature/F04-ci (erlaubt)' $h (BashCall "git push -u origin feature/F04-ci" $repoFeat) 0

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

$h = "block-gh-merge.ps1"
Check 'Huelle: bash -c "gh pr merge 3"' $h (BashCall 'bash -c "gh pr merge 3"' $proj) 2
Check 'Huelle: powershell -c "gh pr merge 3"' $h (BashCall 'powershell -c "gh pr merge 3"' $proj) 2
Check "Huelle: cmd /c gh pr merge 3" $h (BashCall "cmd /c gh pr merge 3" $proj) 2
Check 'Unterbefehl: echo $(gh pr merge 3)' $h (BashCall 'echo $(gh pr merge 3)' $proj) 2
Check "Klammer: (gh pr merge 3)" $h (BashCall "(gh pr merge 3)" $proj) 2
Check "Block: & { gh api -X PUT .../merge }" $h (BashCall "& { gh api -X PUT repos/o/r/pulls/3/merge }" $proj) 2
Check "gh repo delete" $h (BashCall "gh repo delete o/r --yes" $proj) 2
Check "gh secret set" $h (BashCall "gh secret set TOKEN" $proj) 2
Check "gh workflow run" $h (BashCall "gh workflow run ci.yml" $proj) 2
Check "gh release delete" $h (BashCall "gh release delete v1" $proj) 2
Check 'Huelle: bash -c "gh pr view 3" (erlaubt)' $h (BashCall 'bash -c "gh pr view 3"' $proj) 0
Check 'Huelle: bash -c "gh pr create --fill" (erlaubt)' $h (BashCall 'bash -c "gh pr create --fill"' $proj) 0

$h = "block-gh-merge.ps1"
Check 'Zuweisung: $x = gh pr merge 3' $h (BashCall '$x = gh pr merge 3' $proj) 2
Check 'Tiefe: env env env env env gh pr merge' $h (BashCall "env env env env env gh pr merge 3" $proj) 2
Check 'eval "gh pr merge 3"' $h (BashCall 'eval "gh pr merge 3"' $proj) 2
Check 'timeout 5 gh pr merge 3' $h (BashCall "timeout 5 gh pr merge 3" $proj) 2
Check 'Set-Alias g gh; g pr merge 3' $h (BashCall "Set-Alias g gh; g pr merge 3" $proj) 2
Check '$g=gh; & $g pr merge 3' $h (BashCall '$g=''gh''; & $g pr merge 3' $proj) 2
Check 'gh.cmd pr merge 3' $h (BashCall "gh.cmd pr merge 3" $proj) 2
Check 'gh -R o/r api -X PUT merge' $h (BashCall "gh -R o/r api -X PUT repos/o/r/pulls/3/merge" $proj) 2
Check 'gh config set pager' $h (BashCall "gh config set pager 'sh -c x'" $proj) 2
Check 'gh extension exec' $h (BashCall "gh extension exec x" $proj) 2
Check 'gh pr review --approve' $h (BashCall "gh pr review 3 --approve" $proj) 2
Check 'gh pr close' $h (BashCall "gh pr close 3" $proj) 2
Check 'gh pr edit' $h (BashCall "gh pr edit 3 --title x" $proj) 2
Check 'gh issue delete' $h (BashCall "gh issue delete 3" $proj) 2
Check 'gh release create' $h (BashCall "gh release create v1" $proj) 2
Check 'gh repo create' $h (BashCall "gh repo create x --public" $proj) 2
Check 'gh run delete' $h (BashCall "gh run delete 5" $proj) 2
Check 'gh issue view (erlaubt)' $h (BashCall "gh issue view 4" $proj) 0
Check 'gh run list (erlaubt)' $h (BashCall "gh run list" $proj) 0
Check 'gh repo view (erlaubt)' $h (BashCall "gh repo view" $proj) 0
Check 'gh pr list (erlaubt)' $h (BashCall "gh pr list" $proj) 0

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

$h = "block-guard-paths.ps1"
Check "Alias sc auf Hook-Skript" $h (BashCall "sc .claude/hooks/block-env.ps1 'exit 0'" $proj) 2
Check "Alias ac auf settings.json" $h (BashCall "ac .claude/settings.json x" $proj) 2
Check "Alias ni im Hook-Ordner" $h (BashCall "ni .claude/hooks/x.ps1" $proj) 2
Check "Alias ri auf Hook-Skript" $h (BashCall "ri .claude/hooks/block-env.ps1" $proj) 2
Check "Alias mi auf Hook-Skript" $h (BashCall "mi .claude/hooks/block-env.ps1 y.ps1" $proj) 2
Check "copy ueber Hook-Skript" $h (BashCall "copy x.ps1 .claude/hooks/block-env.ps1" $proj) 2
Check "move aus dem Hook-Ordner" $h (BashCall "move .claude/hooks/block-env.ps1 y.ps1" $proj) 2
Check "rmdir Hook-Ordner" $h (BashCall "rmdir .claude/hooks" $proj) 2
Check '.NET [IO.File]::Copy auf Hook-Skript' $h (BashCall '[IO.File]::Copy("a.ps1",".claude/hooks/block-env.ps1",$true)' $proj) 2
Check "node -e writeFileSync auf Hook-Skript" $h (BashCall 'node -e "require(\"fs\").writeFileSync(\".claude/hooks/block-env.ps1\",\"\")"' $proj) 2
Check "Select-String im Hook-Ordner (erlaubt)" $h (BashCall "Select-String -Path .claude/hooks/block-env.ps1 -Pattern exit" $proj) 0
Check "git log fuer Hook-Ordner (erlaubt)" $h (BashCall "git log --oneline -- .claude/hooks" $proj) 0
Check "git commit mit Pfad im Text (erlaubt)" $h (BashCall 'git commit -m "docs: notes on .claude/hooks"' $proj) 0

$hookdir = Join-Path $proj ".claude\hooks"
Check "cd .claude; cd hooks; echo x > a.ps1" $h (BashCall "cd .claude; cd hooks; echo x > a.ps1" $proj) 2
Check "Set-Location .claude, dann relativer Pfad" $h (BashCall "Set-Location .claude; Set-Content hooks/block-env.ps1 x" $proj) 2
Check 'Variable mit .claude, dann sc' $h (BashCall '$p=''.claude''; sc "$p/hooks/x" y' $proj) 2
Check "Platzhalter .cl*/hooks/..." $h (BashCall "sc .cl*/hooks/block-env.ps1 x" $proj) 2
Check "Python-Listenkomprehension mit eckiger Klammer (harmlos)" $h (BashCall 'python -c "print([m for m in x])"' $proj) 0
Check "Remove-Item -Recurse -Force *" $h (BashCall "Remove-Item -Recurse -Force *" $proj) 2
Check "Arbeitsverzeichnis im Hook-Ordner: Umleitung" $h (BashCall "echo x > a.ps1" $hookdir) 2
Check "Arbeitsverzeichnis im Hook-Ordner: Set-Content" $h (BashCall "Set-Content block-env.ps1 x" $hookdir) 2
Check "Arbeitsverzeichnis im Hook-Ordner: cat (erlaubt)" $h (BashCall "cat block-env.ps1" $hookdir) 0
Check "Aufruf ueber Variable" $h (BashCall '$c=''Set-Content''; & $c .claude/hooks/x y' $proj) 2
Check 'bash -c mit Umleitung' $h (BashCall 'bash -c "echo x > .claude/hooks/a"' $proj) 2
Check 'cmd /c copy' $h (BashCall 'cmd /c copy x .claude\hooks\a' $proj) 2
Check "git reset --hard" $h (BashCall "git reset --hard" $proj) 2
Check "git restore ." $h (BashCall "git restore ." $proj) 2
Check "git restore auf Hook-Skript" $h (BashCall "git restore .claude/hooks/block-env.ps1" $proj) 2
Check "git checkout abc123 -- ." $h (BashCall "git checkout abc123 -- ." $proj) 2
Check "git checkout ." $h (BashCall "git checkout ." $proj) 2
Check "git clean -fd" $h (BashCall "git clean -fd" $proj) 2
Check "git stash pop" $h (BashCall "git stash pop" $proj) 2
Check "git apply x.patch" $h (BashCall "git apply x.patch" $proj) 2
Check "git diff --output mit Pfad" $h (BashCall "git diff --output=out.txt .claude/hooks" $proj) 2
Check "git restore -s HEAD~1 (Quelle)" $h (BashCall "git restore -s HEAD~1 docs/x.md" $proj) 2
Check "git restore --source=HEAD~1" $h (BashCall "git restore --source=HEAD~1 docs/x.md" $proj) 2
Check "git restore --staged --worktree" $h (BashCall "git restore --staged --worktree docs/x.md" $proj) 2
Check "git restore -SW" $h (BashCall "git restore -SW docs/x.md" $proj) 2
Check "git restore --staged (erlaubt)" $h (BashCall "git restore --staged docs/x.md" $proj) 0
Check "git restore -S (erlaubt)" $h (BashCall "git restore -S docs/x.md" $proj) 0
Check "git checkout -b (erlaubt)" $h (BashCall "git checkout -b fix/x" $proj) 0
Check "git switch main (erlaubt)" $h (BashCall "git switch main" $proj) 0
Check "git stash (erlaubt)" $h (BashCall "git stash" $proj) 0
Check "git diff --stat (erlaubt)" $h (BashCall "git diff --stat" $proj) 0
Check "git reset (ohne --hard, erlaubt)" $h (BashCall "git reset HEAD docs/x.md" $proj) 0
Check 'Get-ChildItem | ForEach-Object (erlaubt)' $h (BashCall 'Get-ChildItem .claude/hooks | ForEach-Object { $_.Name }' $proj) 0
Check 'gci | % { gc $_ } (erlaubt)' $h (BashCall 'gci .claude/hooks | % { gc $_ }' $proj) 0
Check 'Variable zuweisen, ls (erlaubt)' $h (BashCall '$p = ''.claude''; ls $p' $proj) 0
Check "ls * (erlaubt)" $h (BashCall "ls *" $proj) 0
Check "Remove-Item im Build-Ordner (erlaubt)" $h (BashCall "Remove-Item build/tmp.txt" $proj) 0
Check "Test-Path auf Hook-Skript (erlaubt)" $h (BashCall "Test-Path .claude/hooks/block-env.ps1" $proj) 0
Gap 'Pfad aus Teilen: $a=.cla; $b=ude; Set-Content "$a$b/hooks/x"' $h (BashCall '$a=''.cla''; $b=''ude''; Set-Content "$a$b/hooks/x" y' $proj)

$h = "block-guard-paths.ps1"
Check 'Zuweisung: $null = Remove-Item auf Hook' $h (BashCall '$null = Remove-Item -Force .claude/hooks/block-env.ps1' $proj) 2
Check 'Zuweisung: $r = Set-Content auf Hook' $h (BashCall '$r = Set-Content .claude/hooks/block-env.ps1 ''exit 0''' $proj) 2
Check 'Zuweisung ohne Leerzeichen: $x=sc auf Hook' $h (BashCall '$x=sc .claude/hooks/block-env.ps1 y' $proj) 2
Check 'Zuweisung eines Textes (erlaubt)' $h (BashCall '$p = ''.claude''; ls $p' $proj) 0
Check 'Tiefe: env env env env env sc auf Hook' $h (BashCall 'env env env env env sc .claude/hooks/x y' $proj) 2
Check '$_.Delete() im Hook-Ordner' $h (BashCall 'gci .claude/hooks | % { $_.Delete() }' $proj) 2
Check '$_.MoveTo() im Hook-Ordner' $h (BashCall 'gci .claude/hooks | % { $_.MoveTo("x") }' $proj) 2
Check '$_.FullName (erlaubt)' $h (BashCall 'gci .claude/hooks | % { $_.FullName }' $proj) 0
Check 'Platzhalter auf _common.ps1' $h (BashCall "sc .c*/hooks/_common.ps1 x" $proj) 2
Check 'Platzhalter */hooks/block-main-push.ps1' $h (BashCall "Remove-Item */hooks/block-main-push.ps1" $proj) 2
Check 'git checkout HEAD~3 */hooks/_common.ps1' $h (BashCall "git checkout HEAD~3 */hooks/_common.ps1" $proj) 2
Check 'git -c core.fsmonitor mit .claude' $h (BashCall 'git -c core.fsmonitor="cp x .claude/hooks/block-env.ps1" status' $proj) 2
Check 'rg --pre mit .claude' $h (BashCall "rg --pre bad.cmd KEY .claude/hooks" $proj) 2
Check 'git grep -O mit .claude' $h (BashCall "git grep -Ovim KEY .claude/hooks" $proj) 2
Check 'Backslash im Pfad: sc .cl\aude/hooks/x' $h (BashCall 'sc .cl\aude/hooks/x y' $proj) 2
Check 'Backtick-Ersetzung: echo `rm .claude/...`' $h (BashCall 'echo `rm .claude/hooks/block-env.ps1`' $proj) 2
Check 'Remove-Item -Recurse -Force .' $h (BashCall "Remove-Item -Recurse -Force ." $proj) 2
Check 'rm -rf .' $h (BashCall "rm -rf ." $proj) 2
Check 'Remove-Item -Recurse -Force $PWD' $h (BashCall 'Remove-Item -Recurse -Force $PWD' $proj) 2
Check 'rm -rf build (erlaubt)' $h (BashCall "rm -rf build" $proj) 0
Check 'git rm -rf .' $h (BashCall "git rm -rf ." $proj) 2
Check 'git checkout-index -f -a' $h (BashCall "git checkout-index -f -a" $proj) 2
Check 'git read-tree -u' $h (BashCall "git read-tree -u HEAD~3" $proj) 2
Check 'git reset --ha' $h (BashCall "git reset --ha" $proj) 2
Check 'git checkout -fq x' $h (BashCall "git checkout -fq x" $proj) 2
Check 'git checkout HEAD ./' $h (BashCall "git checkout HEAD ./" $proj) 2
Check 'git checkout HEAD :/' $h (BashCall "git checkout HEAD :/" $proj) 2
Check 'git switch -f main' $h (BashCall "git switch -f main" $proj) 2
Check 'git switch --discard-changes main' $h (BashCall "git switch --discard-changes main" $proj) 2
Check 'git checkout feature/x (erlaubt)' $h (BashCall "git checkout feature/x" $proj) 0
Check 'Tiefe: bash -c in bash -c in ...' $h (BashCall 'bash -c "bash -c \"bash -c \\\"bash -c \\\\\\\"bash -c \\\\\\\\\\\\\\\"sc .claude/hooks/x y\\\\\\\\\\\\\\\"\\\\\\\"\\\"\""' $proj) 2

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
  foreach ($event in $cfg.hooks.PSObject.Properties) {
    foreach ($entry in $event.Value) {
      foreach ($hk in $entry.hooks) {
        $hname = [regex]::Match([string]$hk.command, '\\hooks\\([A-Za-z0-9._-]+\.ps1)').Groups[1].Value
        CheckConfig ("Hook-Aufruf bricht bei Fehler ab (Exit 2): " + $hname) (([string]$hk.command).Contains('if ($LASTEXITCODE -ne 0) { exit 2 }'))
      }
    }
  }
  CheckConfig "Hilfsdatei _common.ps1 vorhanden" (Test-Path (Join-Path $proj ".claude\hooks\_common.ps1"))
  & git -C $proj ls-files --error-unmatch ".claude/hooks/_common.ps1" 2>$null | Out-Null
  CheckConfig "Hilfsdatei _common.ps1 von Git verfolgt" ($LASTEXITCODE -eq 0)
  CheckConfig "Read-Sperre fuer .env vorhanden" (@($cfg.permissions.deny) -contains "Read(**/.env)")
  CheckConfig "Ausnahme fuer .env.example vorhanden" (@($cfg.permissions.deny) -contains "Read(!**/.env.example)")
  $hookCmd = ($cfg.hooks.PreToolUse | ForEach-Object { $_.hooks } | Where-Object { $_.command -like '*block-env.ps1*' } | Select-Object -First 1).command
  $enc = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($hookCmd))
  $savedDir = $env:CLAUDE_PROJECT_DIR
  $env:CLAUDE_PROJECT_DIR = Join-Path ([System.IO.Path]::GetTempPath()) 'kein-projekt'
  '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | & powershell.exe -NoProfile -EncodedCommand $enc 2>$null | Out-Null
  $failCode = $LASTEXITCODE
  $env:CLAUDE_PROJECT_DIR = $savedDir
  CheckConfig ("Hook-Aufruf bei fehlendem Skript endet mit Exit 2 (erhalten " + $failCode + ")") ($failCode -eq 2)
  CheckConfig "Edit-Sperre fuer .claude/hooks vorhanden" (@($cfg.permissions.deny) -contains "Edit(/.claude/hooks/**)")
  CheckConfig "Edit-Sperre fuer settings.json vorhanden" (@($cfg.permissions.deny) -contains "Edit(/.claude/settings.json)")
  CheckConfig "Rueckfrage vor git push vorhanden" (@($cfg.permissions.ask) -contains "Bash(git push *)")
}

""
"Ergebnis: {0} Tests, {1} Fehler" -f $script:total, $script:fail
exit $script:fail
