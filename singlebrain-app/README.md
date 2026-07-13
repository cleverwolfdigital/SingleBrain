# Single Brain — app source

Source for the **production Single Brain dashboard** at
**https://brain.cleverwolfdigital.com** (Hostinger VPS `srv1763128.hstgr.cloud`).

This folder is the version-controlled mirror of `/root/singlebrain-app` on the VPS
so the app has history and a recovery path. **Secrets and the live database are not
here** — they live only in the VPS `.env` and `data/` (both gitignored). The GitHub
`SingleBrain` repo remains the single source of truth for portfolio *data*
(`Personal/Notes/Master_Dashboard.md`); this folder is the *application* that serves it.

## Stack
- **FastAPI + uvicorn** (`app.api:app`) on `127.0.0.1:8000`, single-file frontend.
- **SQLite** (`data/singlebrain.db`) for tasks, journal, and time tracking.
- **Auth:** Magic Link + TOTP 2FA (`app/auth.py`); only `@cleverwolfdigital.com` may log in.
- **CWD Brain Chat:** Grok/xAI, context read live from `Master_Dashboard.md`.
- Fronted by **Traefik + Let's Encrypt** (HTTPS); runs under systemd (`singlebrain-api`).

## Layout
```
app/
  api.py        FastAPI routes: state, tasks, timers, reports, chat
  auth.py       Magic Link + TOTP 2FA gate
  db.py         thin SQLite layer + additive migrations
  schema.sql    data model
  repo.py       git Pull -> Work -> Push against the brain repo
  journal.py    morning/EOD journal -> SQLite + markdown -> push
  seed.py       first-run seed
  config.py     env-driven paths + settings
frontend/index.html   the entire dashboard UI (HTML/CSS/JS, no build step)
requirements.txt
run.sh                legacy Streamlit launcher (NOT the production entrypoint)
dashboard.py          legacy Streamlit prototype (kept for history)
singlebrain-api.service   systemd unit (production entrypoint)
.env.example          required env vars (names only — no secrets)
```

## Deploy a change to production
Files are copied to the VPS; the frontend is read fresh on each request, the
backend needs a restart:
```bash
scp app/api.py        hostinger:/root/singlebrain-app/app/api.py
scp frontend/index.html hostinger:/root/singlebrain-app/frontend/index.html
ssh hostinger "systemctl restart singlebrain-api && systemctl is-active singlebrain-api"
```

## Recover the app from scratch on a fresh VPS
1. `git clone` this repo; copy `singlebrain-app/` to `/root/singlebrain-app`.
2. Clone the data repo to `/root/singlebrain` (`SB_BRAIN_REPO`).
3. `python3 -m venv venv && venv/bin/pip install -r requirements.txt`
4. `cp .env.example .env` and fill in real values (auth secret, Resend key, XAI key).
5. Install `singlebrain-api.service` into `/etc/systemd/system/`, then
   `systemctl daemon-reload && systemctl enable --now singlebrain-api`.
6. Point Traefik/DNS at `brain.cleverwolfdigital.com`. The SQLite DB self-creates
   and migrates on first boot; `seed.py` seeds baseline rows.

> The live SQLite database is **not** in git. Back it up separately
> (`scp hostinger:/root/singlebrain-app/data/singlebrain.db ...`) if you need to
> preserve tasks/journal/time-tracking history across a rebuild.
