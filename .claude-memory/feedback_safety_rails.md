---
name: feedback-safety-rails
description: Commands that require explicit user confirmation before running — never auto-approve
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7988ec26-4c3d-4cef-9545-b814b0c5e8e4
---

Always ask for confirmation before running any of these — never auto-approve:
- Any `drizzle-kit push` or `drizzle-kit migrate` command (risk: schema changes to live TiDB)
- Any Railway CLI deploy commands (`railway up`, `railway deploy`)
- Any secret/env var changes (Railway dashboard or CLI)
- Any destructive DB operations (DROP, TRUNCATE, DELETE without WHERE)

**Why:** App is live in production on Railway with real users. A bad migration or accidental deploy could cause downtime or data loss. User explicitly set: "Keep auto-approve OFF for any DB/deploy/secret commands. Don't run drizzle-kit push."

**How to apply:** Even if the user asks to "just run it," pause and confirm first for this category of commands. The cost of a prompt is low; the cost of an accidental prod migration is high.
