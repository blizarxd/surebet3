@echo off
REM Sube el codigo al VPS y reinicia el escaner (no toca data\ ni .env del VPS).
cd /d "%~dp0"
set VPS=root@65.21.243.40
tar czf - scan.py clv.py dashboard.py aliases.json requirements.txt surebet static deploy | ssh %VPS% "tar xzf - -C /opt/apps/surebet3 && cp /opt/apps/surebet3/deploy/surebet3.service /etc/systemd/system/ && systemctl daemon-reload && systemctl restart surebet3 && systemctl is-active surebet3"
pause
