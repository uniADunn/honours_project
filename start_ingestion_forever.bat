@echo off
setlocal EnableExtensions

set "PROJECT_DIR=C:\Users\ad731\Desktop\crop_lighting\honours_project"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "SCRIPT=%PROJECT_DIR%\scripts\backend-scripts\incoming_sensor_payload.py"
set "PYTHONPATH=%PROJECT_DIR%"

set "LOG_DIR=%PROJECT_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\ingestion_supervisor.log"
set "MUTEX_NAME=Local\HonoursProject_BackendIngestion"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%date% %time%] [START BAT] Supervisor starting...>>"%LOG_FILE%"
echo [%date% %time%] [START BAT] PROJECT_DIR=%PROJECT_DIR%>>"%LOG_FILE%"
echo [%date% %time%] [START BAT] PYTHON=%PYTHON%>>"%LOG_FILE%"
echo [%date% %time%] [START BAT] SCRIPT=%SCRIPT%>>"%LOG_FILE%"

cd /d "%PROJECT_DIR%"
:loop
powershell -NoProfile -Command ^
  "$logFile = '%LOG_FILE%';" ^
  "$py = '%PYTHON%';" ^
  "$script = '%SCRIPT%';" ^
  "$project = '%PROJECT_DIR%';" ^
  "$mutexName = '%MUTEX_NAME%';" ^
  "function Write-Log([string]$msg) { $ts = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'); Add-Content -Path $logFile -Value ('[' + $ts + '] ' + $msg) }" ^
  "$mutexExists = $false; try { $m = [System.Threading.Mutex]::OpenExisting($mutexName); $mutexExists = $true; $m.Close() } catch [System.Threading.WaitHandleCannotBeOpenedException] { } catch { };" ^
  "if ($mutexExists) { Write-Log ('[START BAT] Another instance already running (mutex ' + $mutexName + ')'); exit 2 }" ^
  "Write-Log '[START BAT] Launching ingestion...';" ^
  "try { $p = Start-Process -FilePath $py -ArgumentList @('-u', $script) -WorkingDirectory $project -PassThru -NoNewWindow} catch { Write-Log ('[START BAT] Failed to launch ingestion: ' + $_.Exception.Message); exit 1 }" ^
  "if (-not $p) { Write-Log '[START BAT] Failed to launch ingestion: no process handle'; exit 1 }" ^
  "Start-Sleep -Seconds 1;" ^
  "$exitedEarly = $p.WaitForExit(1000);" ^
  "if ($exitedEarly -or $p.HasExited) { $p.Refresh(); try { $code = [int]$p.ExitCode } catch { $code = 1 }; Write-Log ('[START BAT] Ingestion exited early (code ' + $code + ')') } else { Write-Log ('[START BAT] Ingestion started (pid ' + $p.Id + ')'); $p.WaitForExit(); $p.Refresh(); try { $code = [int]$p.ExitCode } catch { $code = 1; Write-Log '[START BAT] Ingestion exited (code unavailable)' } };" ^
  "exit $code"
set "EC=%ERRORLEVEL%"

REM If lock says "already running", do NOT restart forever
if "%EC%"=="2" (
  echo [%date% %time%] Another instance already running (code 2). Exiting supervisor.>>"%LOG_FILE%"
  exit /b 0
)

echo [%date% %time%] Ingestion exited (code %EC%). Restarting in 5s...>>"%LOG_FILE%"
timeout /t 5 /nobreak >nul
goto loop
