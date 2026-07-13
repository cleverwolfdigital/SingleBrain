---
name: project-singlebrain-dashboard
description: Single Brain dashboard — production is the VPS app at brain.cleverwolfdigital.com; Cloudflare + website/ai/index.html are retired
metadata: 
  node_type: memory
  type: project
  originSessionId: c86dd7d9-e4a4-45cb-b028-123a624c8d4e
---

**Production Single Brain dashboard = the VPS FastAPI app at https://brain.cleverwolfdigital.com** (Hostinger VPS `srv1763128.hstgr.cloud`, IP 2.25.160.217), served via Traefik + Let's Encrypt HTTPS. App lives at `/root/singlebrain-app` (systemd `singlebrain-api`, port 8000).

- **Auth (Phase 2 — COMPLETE):** Magic Link (Resend, `cleverwolfdigital.com` domain verified) + **TOTP authenticator 2FA**; only `@cleverwolfdigital.com` emails may log in.
- **RETIRED / NOT production (do not reference as live):**
  - the Cloudflare Pages site `single-brain.pages.dev`
  - the repo file `website/ai/index.html`
- **Single source of truth = `Master_Dashboard.md`** in GitHub repo `cleverwolfdigital/SingleBrain` — fully merged: tier portfolio (~29 businesses), staff + emails, and `Personal/Notes/Projects/Kimchee_88.md` (full Kimchee #88 business plan + Legal/Branding/Operations/Pre-Opening task list).
- **App source is version-controlled** at `singlebrain-app/` in the SingleBrain repo (mirror of VPS `/root/singlebrain-app`; secrets/`.env`/`data/` DB gitignored). README there documents deploy (`scp` + `systemctl restart singlebrain-api`) and full rebuild/recovery steps. Backend = FastAPI `app.api:app` (uvicorn 127.0.0.1:8000); frontend = single `frontend/index.html` (read fresh per GET, no restart needed).
- **CWD Brain Chat (Grok/xAI)** is live: floating wolf-icon widget + sidebar view. Its context is built in `_state_context()` by reading `Master_Dashboard.md` + `Kimchee_88.md` + live DB tasks (NOT the 4-row DB seed — that was the "only 4 businesses" bug, fixed). Key in VPS `.env` `XAI_API_KEY`, base `https://api.x.ai/v1`, model `grok-4`.
- **Task time tracking + productivity reports** shipped: `tasks` table has `estimate_min`, `actual_sec`, `started_at`, `completed_at` (added via idempotent `db._migrate()`). Endpoints `/api/tasks/{id}/start|pause|complete|reopen` and `/api/reports?period=day|week|month|quarter|year&offset=N` (bucketed in Hawaii time, `HST=UTC-10`). Frontend "Productivity" view + per-task timer buttons. Clicking a business or project card opens a Kimchee-style detail overlay.

**Still-open caveats:**
- **VPS→GitHub push WORKS** (SSH deploy key on VPS; remote is SSH). Earlier "cannot push" issue is resolved. See [[reference-hostinger-ssh]].
- The **old exposed Resend API key** flagged in Quincy's 2026-07-12 journal has been **deleted** (done 2026-07-12). The in-use key (a different `re_3C…`, domain verified) drives magic-link email.
- Multiple parallel Claude Code windows + the VPS both push to `main` — pull/rebase before pushing to avoid non-fast-forward rejects.
- The **live SQLite DB is not in git** (only on VPS at `/root/singlebrain-app/data/`); back it up separately to preserve task/journal/time-tracking history across a rebuild.

**Related project — Chaney Brook deal room:** separate from Single Brain. It's a Next.js/Vercel + Neon Postgres + Prisma web app in repo `github.com/CleverWolfDeveloper/CBrooksCommercial` (PRs #6/#7 merged). Not lost; recoverable from that repo + Claude session `df276a97`.
