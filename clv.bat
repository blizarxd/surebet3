@echo off
REM Informe de CLV: si la ventaja de las apuestas de valor es real. Tambien lo manda a Telegram.
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python clv.py --telegram
pause
