# Decisions Log

Append-only. Newest entries at the top. One dated entry per decision or sync event.
Format: date — what was decided/changed, why, and any follow-up.

---

## 2026-07-12 — Single Brain prototype browser QA completed

**Result:** Verified the installed dashboard at desktop and 390px mobile sizes. Mock
authentication, project filtering, mobile navigation, journal persistence after reload,
and scripted chat responses all pass. No browser warnings, errors, duplicate IDs,
navigation mismatches, or viewport overflows were found.

**Accuracy fix:** Added an explicit "Prototype data" label to the sprint scoreboard because
the outreach, reply, and fit-call counts are placeholders rather than confirmed repo data.

**Follow-up:** Use the local preview for visual review. Live data wiring remains out of scope
until Quincy explicitly prioritizes it after the current sales sprint.

## 2026-07-12 — Single Brain command center prototype completed

**Source:** Quincy explicitly requested a visual frontend prototype for the Single Brain
operating system.

**Decision:** Replace the unrelated `website/ai/index.html` marketing page with a zero-build,
single-file dashboard prototype. Keep the interface anchored to the active sales sprint:
the five canonical revenue metrics and one highest-leverage next action lead the overview.

**Actions taken:**
- Built responsive views for Overview, Businesses, Projects, Staff, Blockers,
  Recommendations, Daily Journal, and Brain Chat.
- Added mock magic-link + 2FA authentication, project filters, local journal persistence,
  mobile navigation, and scripted chat responses.
- Used current repository data as realistic placeholder content; no sends, publishing,
  spending, or external account actions occurred.

**Follow-up:** Review visual direction and connect live repository data only after Quincy
chooses to continue. Do not let dashboard iteration displace the current close-and-invoice
sales sprint.

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
