# TOOLS.md - Local Notes

## Discord

### Server: Clever Wolf Digital
- **Guild ID:** 1276999903721164911

### Team Members
- **Malia** — Discord ID: 691018065655234611
- **Jordan** — Discord ID: 915898884960714762

### Key Channels
- #general — 1276999904291717122
- #websites — 1281284193124618240
- #sales — 1281284265878753381
- #graphics — 1281284280579788851
- #socials — 1361860382838362122
- #ai-convos — 1516838824653885450

## GitHub

### SingleBrain
- **Repo:** git@github.com:cleverwolfdigital/SingleBrain.git
- **Deploy key:** ~/.ssh/singlebrain_deploy (ed25519, read/write)

## Claude Code Skills

Skills live in `skills/` in this repo and must be copied to `~/.claude/skills/` on each machine to activate them.

### Install on a new machine
```bash
git clone https://github.com/cleverwolfdigital/SingleBrain.git
cp -R SingleBrain/skills/* ~/.claude/skills/
```

### Installed skills
| Skill | Description |
|---|---|
| `frontend-design` | Opinionated UI design lead — distinctive palettes, typography, layout. Invoke with `/frontend-design` in Claude Code |

### To add a new skill
```bash
cp -R ~/.claude/skills/<skill-name> ~/SingleBrain/skills/
cd ~/SingleBrain && git add skills/ && git commit -m "add <skill-name> skill" && git push
```

---

## Hosting

### cleverwolfdigital.com
- **Platform:** WordPress on LiteSpeed (Hostinger)
- **/ai/** — static HTML landing page, not WordPress-managed
