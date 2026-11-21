@echo off
REM Double-click this file. First run sets everything up (takes a few minutes).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Setting up for the first time...
    python -m venv .venv || (echo Python not found. Install Python 3.10+ from python.org and tick "Add to PATH". & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
)
echo Fetching new data...
".venv\Scripts\python.exe" ingest.py
echo Opening the dashboard. Close this window to stop it.
".venv\Scripts\python.exe" -m streamlit run app.py
pause
