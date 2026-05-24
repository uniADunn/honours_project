@echo off
REM UPDATE THIS PATH TO MATCH YOUR LOCAL PROJECT LOCATION
set "PROJECT_DIR=C:\your\path\to\your\honours_project"

REM Optional: small delay so networking/NetBird can come up
timeout /t 10 /nobreak >nul

start "" /min cmd.exe /c ""%PROJECT_DIR%\start_ingestion_forever.bat""
exit /b
