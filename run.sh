#!/usr/bin/env bash
# First run sets up a virtual environment, then fetches data and opens the dashboard.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python ingest.py
.venv/bin/python -m streamlit run app.py
