# Single Brain — Vision (PARKED)

**Status:** HELD — not active. Do NOT build from this without Quincy's explicit go.
**Filed:** 2026-07-10
**Source:** Pasted from a claude.ai chat ("Clever Wolf Digital 'Single Brain' Initialization").
**Decision:** See `.claude-memory/decisions-log.md` (2026-07-10) — reconcile into existing brain, hold the expansion.

---

## Why this is parked, not executed

This prompt describes a **different, larger business posture** than the one currently being
sold, and it is a **BUILD** directive during an active **SELL-not-BUILD** sprint
(2026-07-02 → 2026-07-16, 0 closes so far). It conflicts with repo state in three ways:

1. **Fights `AGENTS.md`/`CURRENT_FOCUS.md`** — the read-first rule redirects "build me a
   system/SOP/playbook" requests back to the revenue action.
2. **Un-parks parked offers** — its two product lines (Six-Module System, Workspace
   Config) are the offers `OFFERS_STATE.md` explicitly parks until a deal closes. The active
   frozen offer is the single **AI-Built Lead-Gen Website ($2,500)**.
3. **Duplicates existing memory** — it wants `CLAUDE.md`/`NOW.md`/`/memory/`, which already
   exist here as `AGENTS.md` / `CURRENT_FOCUS.md` / `.claude-memory/`.

## What WAS adopted from it (durable, model-agnostic)

- Decisions log (`.claude-memory/decisions-log.md`) — created.
- "Session deltas from other modalities" protocol + "Where Things Live" map — added to `AGENTS.md`.

## Adopt-worthy later (model-agnostic, not yet imported)

- **Content-integrity standard** (no unverified third-party stats in client-facing material).
  Useful even for the current website offer's outreach copy. Recommend adding to `BOUNDARIES.md`
  or `BRAND_VOICE.md` on Quincy's go.

## Parked with the offer expansion (tied to the six-module / pro-services model)

- A2P 10DLC / TCPA / call-recording compliance gate (only relevant once messaging modules exist).
- Capacity-throttle rule, agency-as-client-zero framing.

## Open decisions required BEFORE any of this could be built

1. **Final six-module pricing** (setup fee + monthly retainer) — not locked.
2. **Lighthouse slot cap** — how many pilot clients delivery capacity actually supports.

---

## Original prompt (verbatim, for the record)

> You are the central operating brain for **Clever Wolf Digital**, an AI agency run by Quincy
> Solano. This session has three jobs, in order: (1) initialize persistent memory so every
> future session starts with full context, (2) build the hard offer document covering all
> services, (3) build the prospecting plan.
>
> **STEP 1 — Initialize persistent memory:** Create `CLAUDE.md` (root business context +
> standing instructions), `NOW.md` (one-screen dashboard: In Flight / Blocked on Quincy /
> Next Up + a "Where Things Live" map), and a `/memory` dir with `offers.md`, `pricing.md`,
> `rules.md`, `pipeline.md`, `assets-inventory.md`, `decisions-log.md`.
>
> Standing instructions: session-open reads NOW first; session-close updates NOW + relevant
> memory + appends a dated decisions-log entry; treat pasted chat summaries as sync input and
> flag conflicts rather than overwrite; this repo is the single source of truth.
>
> **Business context:** Clever Wolf Digital deploys automated AI systems for local
> brick-and-mortar + professional-services businesses. Pre-lighthouse stage; goal 3–5
> lighthouse clients at pilot pricing for documented case results; sales throttled to
> delivery capacity.
>
> - **Product Line 1 — Six-Module AI System** (local B&M): one productized engagement —
>   Database Reactivation, Reviews & Referrals, Website Lead Nurture, AI Receptionist, Sales
>   Trainer, Paid Ads + Nurture. "Seal the bucket before paid ads." Pricing: setup + retainer,
>   not locked. Launch verticals: med spas/aesthetics + HVAC/trades.
> - **Product Line 2 — AI Workspace Configuration** (pro services): Audit & Blueprint **$1,500**
>   one-time (credited to install within 30 days; the qualification gate); Full Installation
>   **from $6,500** one-time, 2–4 weeks; Managed Workspace **from $950/mo**, cancel after 90
>   days. Positioned against a junior hire, not agencies. Not sold: custom software, strategy
>   decks with no artifact, tool-shopping consulting.
> - **Cross-sell:** workspace clients with lead-flow problems → six-module; six-module clients
>   with back-office drag → workspace.
>
> **Hard rules (rules.md):** (1) content integrity — no unverified third-party stats in
> client-facing material; proof = mechanism, client-documented numbers, or independently
> verified research. (2) compliance gate — A2P 10DLC, TCPA opt-out, state call-recording
> consent before any messaging module. (3) agency as client zero. (4) evaluate before
> incorporating. (5) capacity throttle. (6) verified market rates.
>
> **Asset inventory:** Built — GTM strategy, workspace delivery SOP, workspace landing page,
> six-module delivery SOP, onboarding CRM (HTML), client one-pager, message library. Gaps
> (priority) — (1) discovery/sales call script + objection handling; (2) outreach sequences;
> (3) audit-session script; (4) voice-capture config pattern; (5) Ad Library research step;
> (6) onboarding CRM JSON→CRM mapping.
>
> **STEP 2 — Build the hard offer** (`/offers/hard-offer.md`): a named, concrete,
> deadline-and-terms offer per line + cross-sell. Six-module lighthouse (pilot pricing for
> case results, explicit slot cap, compliance built into onboarding, risk-reversal tied to
> delivery milestones NOT revenue outcomes). Workspace ladder ($1,500 audit front door →
> $6,500 install → $950/mo managed; "flagship project built live, yours to keep" as risk
> reversal). Cross-sell paths with trigger conditions. Every proof claim passes content
> integrity.
>
> **STEP 3 — Build the prospecting plan** (`/prospecting/plan.md`): targeted outreach only, no
> paid ads yet. List building per vertical, actual outreach sequences (copy + merge fields +
> timing + reply branches), the demo moment per line (text-the-receptionist / live flagship
> build), cadence + capacity math back-calculated from the lighthouse cap, handoff to the
> discovery call. Stub `/sales/discovery-script.md`.
>
> **STEP 4 — Make it durable:** git init + commit; connect to a private GitHub repo (offer
> the `gh` commands, let Quincy run auth).
>
> **Working style:** ask clarifying questions only where a wrong guess is expensive (final
> six-module pricing, lighthouse slot count); otherwise decide, mark `[ASSUMPTION]`, log it.
> Commit after each step. End with a summary of files + open `[ASSUMPTION]` items.
