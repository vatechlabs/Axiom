@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Backend virtual environment not found. Run the worker from your activated backend terminal instead.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" manage.py process_folder_scans --watch --interval 30
if errorlevel 1 pause
