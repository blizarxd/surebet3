@echo off
REM Lanza el escaner (con cuenta en papel y panel web) y lo reinicia solo si se cae.
REM Cierra esta ventana o Ctrl+C para parar. Panel: http://localhost:8765
cd /d "%~dp0"
echo OJO: el escaner ya corre 24/7 en el VPS (panel: panel_vps.bat). Si lo abres aqui tambien,
echo llegan avisos dobles a Telegram y se gasta el doble de cuota. Cierra esta ventana si no es a proposito.
timeout /t 15
set PYTHONUTF8=1
set ARGS=%*
start "" cmd /c "timeout /t 20 >nul & start http://localhost:8765"
:loop
.venv\Scripts\python scan.py %ARGS%
set ARGS=
echo El escaner se detuvo. Reiniciando en 30 segundos... (Ctrl+C para salir)
timeout /t 30
goto loop
