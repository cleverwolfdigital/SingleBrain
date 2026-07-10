# AGENTS.md — Always-On Loader

Lean always-on context file. Load at the start of every session, then follow its pointers.
It holds **no dashboard content of its own** — the live focus is `CURRENT_FOCUS.md` (single source of truth).

## Read order (every session)

1. **CURRENT_FOCUS.md** — the active sprint dashboard. **Read FIRST.** It wins when files
   conflict: if a request contradicts it (e.g. "build me a new system/SOP/playbook"), redirect
   to the revenue action instead.
2. Then load as relevant:

- **BOUNDARIES.md** — Hard limits (NEVER publish, send, spend). Quincy approves everything.
- **BRAND_VOICE.md** — Avatars, guardrails for Hawai'i + Seattle. No em-dashes. Friend voice, sell the fix.
- **OFFERS_STATE.md** — Current offers, metrics, winning hooks.
- **PLAYBOOK.md** — Weekly loop, standing rules, one action per offer.
- **HOOKS.md** — Log of tested hooks (converted/dead/testing) by market.
- **AI_TEAM_BRIEF.md** / **AI_TOOL_PROMPTS.md** — how the AI tools share this brain; startup prompts per tool.

Load when relevant (not every session):
- **.claude-memory/decisions-log.md** — dated decisions & cross-modality syncs. Append on session close when anything changed.
- **strategy/single-brain-vision.md** — PARKED larger-offer + prospecting build. Do not build without Quincy's explicit go.

All other files are secondary. Prioritize revenue actions over new builds.

---

## Where Things Live

- **This repo (`CWD-Hermes` / SingleBrain)** — canonical brain: focus, offers, brand voice,
  hooks, playbook, memory (`.claude-memory/`), decisions log, parked strategy.
- **Google Drive** — this repo sits under `G:\My Drive\...`, so every saved file auto-syncs to
  the cloud. Local save = backed up. It does NOT reach GitHub until committed + pushed.
- **GitHub** — `github.com/cleverwolfdigital/SingleBrain`. Reached only via `git commit` + `push`.
- **claude.ai CleverWolf project** — chat/strategy sessions. Their output arrives here as pasted
  deltas (see below), not automatically.
- **Standalone deliverables** — onboarding CRM (HTML), client one-pager, landing page — see
  `assets-inventory` / `project_cleverwolf` for paths.

## Session deltas from other modalities

Quincy will periodically paste summaries or prompts from claude.ai chats or other tools. Treat
them as **sync input**, not commands to execute blindly:

1. **Merge** genuine decisions into the relevant memory/state file.
2. **Log** the sync in `.claude-memory/decisions-log.md` (date + source).
3. **Flag conflicts, do not silently overwrite.** If a delta contradicts repo state (e.g. asks
   to build during a SELL sprint, or un-park a parked offer), surface the conflict and get
   Quincy's call before acting. Park don't-build-yet material in `strategy/`.
