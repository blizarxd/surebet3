@echo off
REM Panel del escaner que corre en el VPS, por tunel SSH: http://localhost:8765
REM (el panel escucha solo en 127.0.0.1 del VPS; deja esta ventana abierta mientras lo miras)
set VPS=root@65.21.243.40
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8765"
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 8765:127.0.0.1:8790 %VPS%
echo Tunel cerrado. (Si dice que el puerto 8765 esta ocupado, cierra el escaner local.)
pause
