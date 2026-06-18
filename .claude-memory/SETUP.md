# Claude Code Memory — New Machine Setup

Claude Code memory is stored locally and doesn't follow you between computers.
This folder is the source of truth. After cloning SingleBrain on a new machine,
run the command below to restore full context.

## Windows (PowerShell)

```powershell
$dest = "$env:USERPROFILE\.claude\projects\C--Users-tidas-opalahoa\memory"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item "$PSScriptRoot\*" $dest -Exclude "SETUP.md" -Force
Write-Host "Claude memory restored to $dest"
```

## Mac / Linux (bash)

```bash
dest="$HOME/.claude/projects/C--Users-tidas-opalahoa/memory"
mkdir -p "$dest"
cp $(dirname "$0")/*.md "$dest/"
rm "$dest/SETUP.md"
echo "Claude memory restored to $dest"
```

## After restoring

Open Claude Code and verify with: "tell me what you remember about this project."

## Keeping it current

When Claude Code writes new or updated memory files, copy them back here and commit:

```powershell
Copy-Item "$env:USERPROFILE\.claude\projects\C--Users-tidas-opalahoa\memory\*" "$(Split-Path $MyInvocation.MyCommand.Path)\" -Force
```
