@echo off
REM Open the dashboard without fetching new data. Run run.bat once first.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m streamlit run app.py
pause
