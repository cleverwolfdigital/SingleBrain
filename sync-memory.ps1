$src  = "$env:USERPROFILE\.claude\projects\C--Users-tidas-opalahoa\memory"
$dest = "$PSScriptRoot\.claude-memory"

Copy-Item "$src\*" $dest -Exclude "SETUP.md" -Force

Set-Location $PSScriptRoot
git add .claude-memory/
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm"
    git commit -m "chore: sync Claude memory ($ts)"
    git pull --rebase origin main
    git push origin main
    Write-Host "Memory synced and pushed." -ForegroundColor Green
} else {
    Write-Host "No memory changes to sync." -ForegroundColor Yellow
}
