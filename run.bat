@echo off
cd /d "%~dp0"
echo Agent Farm  -  http://localhost:8000/  (greenhouse + logistics tabs)
echo.
echo Starting local mongod (127.0.0.1:27017)...
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\start-mongo.ps1"
timeout /t 1 >nul
echo Starting OR-Tools optimizer on 8080 in a new window...
start "agent-farm: optimizer" cmd /k ".venv\Scripts\python.exe -m uvicorn optimizer.main:app --host 127.0.0.1 --port 8080"
timeout /t 2 >nul
echo Starting Transport Agent on 8001 in a new window...
start "agent-farm: transport" cmd /k "set OPTIMIZER_URL=http://localhost:8080&& .venv\Scripts\python.exe -m uvicorn app.transport.main:app --host 127.0.0.1 --port 8001"
timeout /t 2 >nul
echo Starting Merchant Agent on 8002 in a new window...
start "agent-farm: merchant" cmd /k ".venv\Scripts\python.exe -m uvicorn app.merchant.main:app --host 127.0.0.1 --port 8002"
timeout /t 2 >nul
echo Starting Greenhouse Agent on 8000 (this window)...
".venv\Scripts\python.exe" -m uvicorn app.greenhouse.main:app --reload --host 127.0.0.1 --port 8000
