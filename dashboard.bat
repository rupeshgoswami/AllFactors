@echo off
REM Open the dashboard without fetching. If there is no database yet, download the latest snapshot from GitHub.
cd /d "%~dp0"
if not exist "data\portal.db" ".venv\Scripts\python.exe" sync_data.py pull
".venv\Scripts\python.exe" -m streamlit run app.py
pause
