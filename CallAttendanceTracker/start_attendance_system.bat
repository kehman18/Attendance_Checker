@echo off

set "PROJECT_DIR=C:\Users\hp\Desktop\CallAttendanceTracker"

start "Attendance Tracker" /D "%PROJECT_DIR%" cmd /k %PROJECT_DIR%\.venv\Scripts\python.exe attendance_tracker.py

timeout /t 3 /nobreak > nul

start "Attendance Report Bot" /D "%PROJECT_DIR%" cmd /k %PROJECT_DIR%\.venv\Scripts\python.exe report_bot.py