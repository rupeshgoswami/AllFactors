@echo off
REM Daily update: fetch new data, then publish the database to GitHub. Log: data\update.log
cd /d "%~dp0"
echo ==== %date% %time% ==== >> data\update.log
".venv\Scripts\python.exe" ingest.py >> data\update.log 2>&1 || exit /b 1
".venv\Scripts\python.exe" sync_data.py publish >> data\update.log 2>&1
