@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

:: ============================================================
:: 一键停止脚本 — Windows
:: ============================================================

set "PROJECT_ROOT=%~dp0"
set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

echo.
echo 正在停止服务...

:: --- 按端口杀进程（与 start.bat 一致） ---
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5409 ^| findstr LISTENING 2^>nul') do taskkill /F /PID %%a >nul 2>&1
echo   OK 端口 5409 已清理

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5173 ^| findstr LISTENING 2^>nul') do taskkill /F /PID %%a >nul 2>&1
echo   OK 端口 5173 已清理

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5410 ^| findstr LISTENING 2^>nul') do taskkill /F /PID %%a >nul 2>&1
echo   OK 端口 5410 已清理

:: --- 清理 PID 文件 ---
if exist "%PROJECT_ROOT%\.backend.pid" del /f "%PROJECT_ROOT%\.backend.pid" >nul 2>&1
if exist "%PROJECT_ROOT%\.frontend.pid" del /f "%PROJECT_ROOT%\.frontend.pid" >nul 2>&1
if exist "%PROJECT_ROOT%\.mcp.pid" del /f "%PROJECT_ROOT%\.mcp.pid" >nul 2>&1

echo.
echo 所有服务已停止
echo.
pause
