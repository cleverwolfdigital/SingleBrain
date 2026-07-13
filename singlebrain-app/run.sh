#!/usr/bin/env bash
# Launch the Single Brain dashboard on the VPS.
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
exec venv/bin/streamlit run dashboard.py
