@echo off
REM Solo el panel web (sin escanear): http://localhost:8765
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python dashboard.py
