@echo off
cd /d "%~dp0"
echo Agent Greenhouse  -  http://localhost:8000/
echo project root: %~dp0
echo.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
