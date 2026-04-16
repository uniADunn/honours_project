@echo off
set "PROJECT_DIR=C:\Users\ad731\Desktop\crop_lighting\honours_project"

REM Optional: small delay so networking/NetBird can come up
timeout /t 10 /nobreak >nul

start "" /min cmd.exe /c ""%PROJECT_DIR%\start_ingestion_forever.bat""
exit /b
