---
name: project-deployment-state
description: "Current Railway deployment state as of 2026-06-14 — what works, what's missing, what needs rotation"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7988ec26-4c3d-4cef-9545-b814b0c5e8e4
---

**As of 2026-06-15:** opalahoa.com is LIVE on Railway, fully migrated off Manus.

**Working:**
- App running on Railway
- TiDB connected (SSL config fix committed: `62e701c`)
- Email/password login works
- www.opalahoa.com serves over HTTPS
- Bare domain (opalahoa.com) 301-redirects to www
- `VITE_APP_ID="opalahoa"` added to Railway (fixes sessions being rejected after login)
- Google OAuth env vars set in Railway (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI=https://www.opalahoa.com/api/auth/google/callback`)
- 6 referral tables migrated to production TiDB via `scripts/migrate-referrals.mjs` (2026-06-15)

**Remaining tasks (in order):**
1. ~~Add `R2_SECRET_ACCESS_KEY` to Railway~~ ✅ Done 2026-06-15
2. ~~Add Square creds to Railway~~ ✅ Done 2026-06-15
3. ~~Rotate exposed secrets~~ ✅ Done 2026-06-15

**Google OAuth route details** (server/_core/googleOAuth.ts):
- Initiate: `GET /api/auth/google`
- Callback: `GET /api/auth/google/callback`
- Reads: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` (optional override)

**Why:** Migrated off Manus platform to Railway for full control. Manus env vars are no longer auto-injected.

**How to apply:** When asked about env vars or credentials, check this list. R2 and Square are still non-functional until their creds are added to Railway.
