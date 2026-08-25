@echo off
cd /d "%~dp0backend"
if not exist .venv (
  echo Backend virtual environment not found. Run run_backend.bat first.
  exit /b 1
)
call .venv\Scripts\activate
python -B -m app.backup %*
pause

