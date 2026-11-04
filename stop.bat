@echo off
rem Double-click to stop the screener servers (ports 8031 and 5191).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -Stop
pause
