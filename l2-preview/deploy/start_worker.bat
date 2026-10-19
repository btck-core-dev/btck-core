@echo off
setlocal
if "%~1"=="" (
  echo Usage: deploy\start_worker.bat C:\private\worker-config.json [--check]
  exit /b 2
)
set "PYTHONPATH=%~dp0..\src;%PYTHONPATH%"
python -m worker.resident_worker --config "%~1" %2
exit /b %errorlevel%
