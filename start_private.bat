@echo off
setlocal EnableDelayedExpansion

set "PROJECT_ROOT=%~dp0"
set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "BACKEND_DIR=%PROJECT_ROOT%\backend"
set "FRONTEND_DIR=%PROJECT_ROOT%\frontend"
set "MCP_DIR=%PROJECT_ROOT%\backend-mcp"

:: --- PATH ---
if exist "%PROJECT_ROOT%\dependency\bin" set "PATH=%PROJECT_ROOT%\dependency\bin;%PATH%"
if exist "%PROJECT_ROOT%\dependency\python" set "PATH=%PROJECT_ROOT%\dependency\python;%PATH%"
if exist "%PROJECT_ROOT%\dependency\node" set "PATH=%PROJECT_ROOT%\dependency\node;%PATH%"
if exist "%PROJECT_ROOT%\dependency\cloakbrowser\chrome.exe" set "CLOAKBROWSER_BINARY_PATH=%PROJECT_ROOT%\dependency\cloakbrowser\chrome.exe"

:: --- Clear proxy ---
set http_proxy=
set https_proxy=
set all_proxy=
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable /t REG_DWORD /d 0 /f >nul 2>&1

:: --- Check ---
echo.
where python >nul 2>&1
if %errorlevel% neq 0 (echo X Python not found & pause & exit /b 1)
where node >nul 2>&1
if %errorlevel% neq 0 (echo X Node.js not found & pause & exit /b 1)
echo OK runtime ready

:: --- Kill old processes by port ---
echo.
echo Clearing ports...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5409 ^| findstr LISTENING 2^>nul') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5173 ^| findstr LISTENING 2^>nul') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5410 ^| findstr LISTENING 2^>nul') do taskkill /F /PID %%a >nul 2>&1
echo OK ports cleared

:: --- Backend venv ---
set "VENV_DIR=%BACKEND_DIR%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"
set "PIP_MIRROR=https://mirrors.aliyun.com/pypi/simple/"

set "VENV_OK=0"
if exist "%VENV_DIR%" if exist "%VENV_PIP%" (
    "%VENV_PYTHON%" -c "import flask" >nul 2>&1
    if !errorlevel! equ 0 set "VENV_OK=1"
)

if "!VENV_OK!"=="0" (
    if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%" >nul 2>&1
    echo Creating venv...
    python -m venv "%VENV_DIR%" --clear
    echo Installing Python dependencies...
    "%VENV_PIP%" install -r "%BACKEND_DIR%\requirements.txt" --no-cache-dir -i "%PIP_MIRROR%"
)
echo OK backend ready

:: --- Frontend deps ---
if not exist "%FRONTEND_DIR%\node_modules" (
    cd /d "%FRONTEND_DIR%"
    call npm install --prefer-offline --registry=https://registry.npmmirror.com
    cd /d "%PROJECT_ROOT%"
)
echo OK frontend ready

:: --- MCP deps + build ---
if not exist "%MCP_DIR%\node_modules" (
    cd /d "%MCP_DIR%"
    call npm install --prefer-offline --registry=https://registry.npmmirror.com
)
cd /d "%MCP_DIR%"
call npm run build
cd /d "%PROJECT_ROOT%"
echo OK mcp ready

:: --- Start services ---
echo.
echo Starting services...
timeout /t 1 /nobreak >nul

set "BACKEND_LOG=%PROJECT_ROOT%\backend.log"
set "FRONTEND_LOG=%PROJECT_ROOT%\frontend.log"
set "MCP_LOG=%PROJECT_ROOT%\mcp.log"
set "TRANSPORT_MODE=sse"

cd /d "%BACKEND_DIR%"
start "SAU-Backend" /B cmd /c ""%VENV_PYTHON%" app.py > "%BACKEND_LOG%" 2>&1"
timeout /t 2 /nobreak >nul
set "BACKEND_PID="
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5409 ^| findstr LISTENING 2^>nul') do set "BACKEND_PID=%%a"
echo   OK Backend PID: !BACKEND_PID!

cd /d "%FRONTEND_DIR%"
start "SAU-Frontend" /B cmd /c "npm run dev > "%FRONTEND_LOG%" 2>&1"
timeout /t 2 /nobreak >nul
set "FRONTEND_PID="
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5173 ^| findstr LISTENING 2^>nul') do set "FRONTEND_PID=%%a"
echo   OK Frontend PID: !FRONTEND_PID!

cd /d "%MCP_DIR%"
start "SAU-MCP" /B cmd /c "set TRANSPORT_MODE=%TRANSPORT_MODE%&& npm start > "%MCP_LOG%" 2>&1"
timeout /t 2 /nobreak >nul
set "MCP_PID="
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5410 ^| findstr LISTENING 2^>nul') do set "MCP_PID=%%a"
if defined MCP_PID (
    echo   OK MCP PID: !MCP_PID!
) else (
    echo   OK MCP started
)

:: Save PIDs
if defined BACKEND_PID echo !BACKEND_PID!> "%PROJECT_ROOT%\.backend.pid"
if defined FRONTEND_PID echo !FRONTEND_PID!> "%PROJECT_ROOT%\.frontend.pid"
if defined MCP_PID echo !MCP_PID!> "%PROJECT_ROOT%\.mcp.pid"

:: Wait for backend
echo.
echo Waiting for backend...
set /a "COUNT=0"
:wait_loop
set /a "COUNT+=1"
if !COUNT! GTR 30 (echo Startup timeout & goto show_info)
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:5409/api/health 2>nul | findstr "200" >nul
if !errorlevel! neq 0 (timeout /t 1 /nobreak >nul & goto wait_loop)

:show_info
echo.
echo ============================================
echo   Frontend:  http://localhost:5173
echo   Backend:   http://localhost:5409
echo   MCP SSE:   http://localhost:5410/sse
echo ============================================
echo.
echo Press Ctrl+C to stop all services
echo.
cd /d "%PROJECT_ROOT%"
powershell -Command "Get-Content '%BACKEND_LOG%' -Wait -Tail 50"
