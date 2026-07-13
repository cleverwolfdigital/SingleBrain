#!/usr/bin/env bash
# Scheduled Single Brain sync: generate this month's recurring tasks, then pull
# latest from GitHub and push the current task state back. Config defaults in
# app/config.py already match the VPS layout, so no .env sourcing is needed.
set -euo pipefail
cd /root/singlebrain-app
venv/bin/python - <<'PY'
from app import db, catalog, api
db.init_db()
created = catalog.generate_recurring()
pushed = api._sync_tasks_to_repo()
print(f"[cron_sync] recurring_created={created} pushed={pushed}", flush=True)
PY
