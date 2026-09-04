@echo off

echo.
echo Stopping services...

:: Kill by port
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5409 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
echo   Port 5409 cleared

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5173 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
echo   Port 5173 cleared

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5410 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
echo   Port 5410 cleared

echo.
echo All services stopped
echo.
pause
