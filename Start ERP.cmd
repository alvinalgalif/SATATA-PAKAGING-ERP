@echo off
setlocal
cd /d "%~dp0"
if exist "C:\Python314\python.exe" (
  "C:\Python314\python.exe" server.py
) else (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 server.py
  ) else (
    python server.py
  )
)
pause
