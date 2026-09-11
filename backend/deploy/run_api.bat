@echo off
REM =====================================================================
REM  QianFanSync IIS 后端启动脚本（发布到 IIS 后使用）
REM  由 deploy\install_iis_task.ps1 注册为「仅在用户登录时运行」的计划任务，
REM  确保后端运行在用户交互会话（Session > 0），CloakBrowser 才能弹出浏览器窗口，
REM  否则「添加账号 / 登录 / 发布」会卡在登录中。
REM
REM  用法：
REM    1) 把 backend 发布内容拷到 IIS 站点目录（如 D:\wwwroot\Upload.API）
REM    2) 把本脚本一并拷到该目录（与 app.py 同级）
REM    3) 运行 deploy\install_iis_task.ps1 注册计划任务
REM =====================================================================
setlocal
cd /d %~dp0

REM ---------- 可配置项（按需修改） ----------
REM 后端监听端口，IIS 站点通过 web.config 反向代理到此端口
set "SAU_PORT=6605"
REM 数据目录（SQLite/上传/日志）：默认指向源码 data，与本地后端共用同一份数据
set "SAU_DATA_DIR=D:\QianFanSyncData"
REM CloakBrowser 浏览器二进制（随发布目录携带）
set "CLOAKBROWSER_BINARY_PATH=%~dp0cloakbrowser\chrome.exe"
REM 使用发布目录自带的虚拟环境（若无则退回系统 python）
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

REM ---------- 环境变量（CloakBrowser / 平台驱动依赖） ----------
set "PATH=%~dp0dependency\bin;%PATH%"

echo [run_api.bat] SAU_PORT=%SAU_PORT% DATA=%SAU_DATA_DIR%
echo [run_api.bat] 当前会话 ID：请确认 >0（Session 0 无法弹浏览器窗口）
start "QianFanSync-6605" /b "%~dp0.venv\Scripts\pythonw.exe" app.py
exit /b 0
endlocal
