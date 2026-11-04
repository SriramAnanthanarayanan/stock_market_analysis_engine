@echo off
rem Double-click to start the screener (backend + frontend) and open the chart UI.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
