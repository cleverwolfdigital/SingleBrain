# Decisions Log

Append-only. Newest entries at the top. One dated entry per decision or sync event.
Format: date — what was decided/changed, why, and any follow-up.

---

## 2026-07-10 — "Single Brain" init prompt: RECONCILE, don't build

**Source:** Quincy pasted a claude.ai chat prompt ("Clever Wolf Digital Single Brain
Initialization") — a full BUILD directive (new memory system + two-line hard offer +
prospecting plan + sales-script stubs).

**Conflict:** Directly opposes the active SELL-not-BUILD sprint (2026-07-02 → 2026-07-16,
0 closes) and un-parks offers `OFFERS_STATE.md` deliberately parks. Its `CLAUDE.md`/`NOW.md`/
`/memory/` also duplicate the existing `AGENTS.md`/`CURRENT_FOCUS.md`/`.claude-memory/`.

**Decision (Quincy):** RECONCILE into the existing brain. Keep the sprint. Adopt only the
durable, model-agnostic pieces. **HOLD** the offer + prospecting expansion for an explicit go.

**Actions taken:**
- Parked the full prompt at `strategy/single-brain-vision.md` (status: HELD).
- Created this decisions log.
- Added "Where Things Live" + "session deltas from other modalities" protocol to `AGENTS.md`.
- De-staled `CURRENT_FOCUS.md` (was still on the old 06-19 → 07-02 window).

**Follow-up / open items:**
- Before any offer build: lock **final six-module pricing** and the **lighthouse slot cap**.
- Recommend adopting the content-integrity standard into `BOUNDARIES.md` (needs Quincy's go).
- Duplication flag: RESOLVED (2026-07-10) — `CURRENT_FOCUS.md` is now the single canonical
  dashboard; `AGENTS.md` slimmed to a lean loader that points to it. Also removed three empty
  leftover folders from the abandoned scp attempt (`Personal/Notes`, `CWD-Hermes/Reference`,
  `CWD-Hermes/Agents`).

## 2026-07-10 — Abandoned the SingleBrain re-clone / server file recovery

**What:** A plan to rename `CWD-Hermes` and re-clone from GitHub, then `scp` four files
(`AI_Orchestration.md`, `Operations_Dashboard.md`, `Agent_Rules.md`, `Hermes.md`) from a
server (`root@srv1763128.hstgr.cloud`, Hostinger).

**Why abandoned:**
- The repo was already fully in sync with `origin/main` (`d5ab986`); a re-clone was a no-op.
- The four "missing" files are **not in the SingleBrain repo on any branch** — re-cloning
  could never recover them.
- The `scp` pulls failed on auth: root password rejected repeatedly (likely key-only root).

**Result:** No files changed on disk; destination folders `Personal/Notes`,
`CWD-Hermes/Reference`, `CWD-Hermes/Agents` were created (empty) and remain.

**Follow-up:** If those four files actually matter, get working server auth (SSH key added
via the Hostinger panel) or pull them via the Hostinger web console — then decide where they
belong (they'd land locally → Google Drive auto-syncs; commit+push to reach GitHub).
