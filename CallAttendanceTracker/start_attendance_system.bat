@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"

cd /d "%PROJECT_DIR%"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Virtual environment not found.
    echo Expected: %PYTHON_EXE%
    pause
    exit /b 1
)

if not exist "%PROJECT_DIR%attendance_tracker.py" (
    echo ERROR: attendance_tracker.py was not found.
    pause
    exit /b 1
)

if not exist "%PROJECT_DIR%report_bot.py" (
    echo ERROR: report_bot.py was not found.
    pause
    exit /b 1
)

start "Telegram Attendance Tracker" cmd /k ""%PYTHON_EXE%" "%PROJECT_DIR%attendance_tracker.py""
timeout /t 3 /nobreak >nul
start "Telegram Report Bot" cmd /k ""%PYTHON_EXE%" "%PROJECT_DIR%report_bot.py""