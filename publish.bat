@echo on
chcp 936 >nul
title QianFanSync IIS 一键发布

REM ========== 配置（按需修改） ==========
set "HOST=192.168.3.8"
set "API_PORT=6603"
set "WEB_PORT=6604"
set "API_INTERNAL_PORT=6605"
set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"
set "API_DIR=D:\wwwroot\Upload.API"
set "WEB_DIR=D:\wwwroot\Upload.Web"
set "TASK_NAME=QianFanSyncUploadAPI"
set "DATA_DIR=D:\QianFanSyncData"

echo.
echo =============== QianFanSync IIS 一键发布 ===============
echo  Web: http://%HOST%:%WEB_PORT%/
echo  API: http://%HOST%:%API_PORT%/
echo.

REM ---------- 1/5 构建前端 ----------
echo [1/5] 构建前端...
cd /d "%REPO%\frontend" || goto :fail
if not exist node_modules (
  echo       安装前端依赖...
  call npm install || goto :fail
)
> .env.production echo VITE_API_BASE_URL=http://%HOST%:%API_PORT%
call npm run build || goto :fail

REM ---------- 2/5 停掉旧 API 进程 ----------
echo [2/5] 停止旧 API 进程...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter 'Name = ''python.exe''' | Where-Object { $_.CommandLine -like '*Upload.API*app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 /nobreak >nul

REM ---------- 3/5 同步后端 ----------
echo [3/5] 同步后端代码到 %API_DIR% ...
robocopy "%REPO%\backend" "%API_DIR%" /MIR /XD data .venv __pycache__ .pytest_cache /NFL /NDL /NJH /NJS >nul
if errorlevel 8 goto :fail
if not exist "%DATA_DIR%\db\database.db" (
  echo       首次初始化数据目录（数据库/cookies）...
  robocopy "%REPO%\data" "%DATA_DIR%" /E /NFL /NDL /NJH /NJS >nul
  if errorlevel 8 goto :fail
)
if not exist "%API_DIR%\.venv\Scripts\python.exe" (
  echo       首次复制 Python 虚拟环境（约 300MB，仅首次）...
  robocopy "%REPO%\backend\.venv" "%API_DIR%\.venv" /E /NFL /NDL /NJH /NJS >nul
  if errorlevel 8 goto :fail
)
if not exist "%API_DIR%\cloakbrowser\chrome.exe" (
  echo       首次复制 CloakBrowser（约 540MB）...
  robocopy "%REPO%\dependency\cloakbrowser" "%API_DIR%\cloakbrowser" /E /NFL /NDL /NJH /NJS >nul
  if errorlevel 8 goto :fail
)

REM 生成运行时启动脚本
> "%API_DIR%\run_api.bat" echo @echo off
>> "%API_DIR%\run_api.bat" echo cd /d %API_DIR%
>> "%API_DIR%\run_api.bat" echo set "PATH=%REPO%\dependency\bin;%PATH%"
>> "%API_DIR%\run_api.bat" echo set "SAU_PORT=%API_INTERNAL_PORT%"
>> "%API_DIR%\run_api.bat" echo set "SAU_DATA_DIR=%DATA_DIR%"
>> "%API_DIR%\run_api.bat" echo set "CLOAKBROWSER_BINARY_PATH=%API_DIR%\cloakbrowser\chrome.exe"
>> "%API_DIR%\run_api.bat" echo start "QianFanSync-6605" /b .venv\Scripts\pythonw.exe app.py
>> "%API_DIR%\run_api.bat" echo exit /b 0

REM 生成 IIS 反向代理配置
> "%API_DIR%\web.config" echo ^<?xml version="1.0" encoding="UTF-8"?^>
>> "%API_DIR%\web.config" echo ^<configuration^>
>> "%API_DIR%\web.config" echo   ^<system.web^>
>> "%API_DIR%\web.config" echo     ^<httpRuntime executionTimeout="900" /^>
>> "%API_DIR%\web.config" echo   ^</system.web^>
>> "%API_DIR%\web.config" echo   ^<system.webServer^>
>> "%API_DIR%\web.config" echo     ^<rewrite^>
>> "%API_DIR%\web.config" echo       ^<rules^>
>> "%API_DIR%\web.config" echo         ^<rule name="ProxyToFlask" stopProcessing="true"^>
>> "%API_DIR%\web.config" echo           ^<match url="^(.*)" /^>
>> "%API_DIR%\web.config" echo           ^<action type="Rewrite" url="http://127.0.0.1:%API_INTERNAL_PORT%/{R:1}" /^>
>> "%API_DIR%\web.config" echo         ^</rule^>
>> "%API_DIR%\web.config" echo       ^</rules^>
>> "%API_DIR%\web.config" echo     ^</rewrite^>
>> "%API_DIR%\web.config" echo   ^</system.webServer^>
>> "%API_DIR%\web.config" echo ^</configuration^>

REM ---------- 4/5 IIS 站点 + 计划任务 + 启动 ----------
echo [4/5] 配置 IIS 站点与计划任务...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ac = 'C:\Windows\System32\inetsrv\appcmd.exe'; if (-not (Test-Path '%WEB_DIR%')) { New-Item -ItemType Directory -Path '%WEB_DIR%' -Force | Out-Null }; if (-not (Test-Path '%API_DIR%')) { New-Item -ItemType Directory -Path '%API_DIR%' -Force | Out-Null }; & $ac add apppool 'Upload.Web' 2>$null | Out-Null; & $ac set apppool 'Upload.Web' /autoStart:true | Out-Null; & $ac add apppool 'Upload.API' 2>$null | Out-Null; & $ac set apppool 'Upload.API' /autoStart:true | Out-Null; & $ac add site /name:'Upload.Web' /bindings:'http://*:%WEB_PORT%' /physicalPath:'%WEB_DIR%' 2>$null | Out-Null; & $ac set site 'Upload.Web' /bindings:'http://*:%WEB_PORT%' | Out-Null; & $ac set app 'Upload.Web/' /applicationPool:'Upload.Web' | Out-Null; if ($LASTEXITCODE -ne 0) { throw 'appcmd Upload.Web failed' }; & $ac add site /name:'Upload.API' /bindings:'http://*:%API_PORT%' /physicalPath:'%API_DIR%' 2>$null | Out-Null; & $ac set site 'Upload.API' /bindings:'http://*:%API_PORT%' | Out-Null; & $ac set app 'Upload.API/' /applicationPool:'Upload.API' | Out-Null; if ($LASTEXITCODE -ne 0) { throw 'appcmd Upload.API failed' }; $action = New-ScheduledTaskAction -Execute '%API_DIR%\run_api.bat'; $trigger = New-ScheduledTaskTrigger -AtLogOn -User ($env:USERDOMAIN + '\' + $env:USERNAME); $principal = New-ScheduledTaskPrincipal -UserId ($env:USERDOMAIN + '\' + $env:USERNAME) -LogonType Interactive -RunLevel Limited; $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; try { Write-Output ('UID-DEBUG=[' + $principal.UserId + ']'); Register-ScheduledTask -TaskName '%TASK_NAME%' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null; $ex = Export-ScheduledTask -TaskName '%TASK_NAME%'; $xx = if ($ex -is [string]) { $ex } else { $ex.Xml }; if (-not $xx) { throw 'export empty' }; $xx = $xx -replace '<ExecutionTimeLimit>[^<]*</ExecutionTimeLimit>', '<ExecutionTimeLimit>PT0H</ExecutionTimeLimit>'; if ($xx -notmatch '<ExecutionTimeLimit>') { $xx = $xx -replace '<Settings>', '<Settings><ExecutionTimeLimit>PT0H</ExecutionTimeLimit>' }; Register-ScheduledTask -TaskName '%TASK_NAME%' -Xml $xx -Force | Out-Null; Stop-ScheduledTask -TaskName '%TASK_NAME%' -ErrorAction SilentlyContinue; Start-ScheduledTask -TaskName '%TASK_NAME%'; $chk = Export-ScheduledTask -TaskName '%TASK_NAME%'; $cx = if ($chk -is [string]) { $chk } else { $chk.Xml }; if ($cx -notmatch '<ExecutionTimeLimit>PT0[HS]</ExecutionTimeLimit>') { throw 'ExecutionTimeLimit PT0 not applied' }; } catch { throw ('task setup failed: ' + $_.Exception.Message) }; Write-Output 'IIS 站点与计划任务就绪'" || goto :fail

REM ---------- 5/5 同步前端 dist + 健康检查 ----------
echo [5/5] 同步前端 dist 到 %WEB_DIR% ...
robocopy "%REPO%\frontend\dist" "%WEB_DIR%" /MIR /NFL /NDL /NJH /NJS >nul
if errorlevel 8 goto :fail

echo 等待 API 上线（内部端口 %API_INTERNAL_PORT%）...
set /a tries=0
:waitloop
set /a tries+=1
curl -sf -o nul "http://127.0.0.1:%API_INTERNAL_PORT%/api/health" >nul 2>&1 && goto :done
if %tries% geq 30 goto :fail
timeout /t 1 /nobreak >nul
goto :waitloop
:done

echo.
echo =============== 发布成功 ===============
echo  Web: http://%HOST%:%WEB_PORT%/
echo  API: http://%HOST%:%API_PORT%/api/health
curl -sf -o nul "http://%HOST%:%API_PORT%/api/health" >nul 2>&1 && echo  IIS 代理链路正常 || echo  [提示] IIS 代理暂未就绪，稍等几秒再访问
echo  API 进程由计划任务 %TASK_NAME% 托管（开机自启，内部端口 %API_INTERNAL_PORT%）
pause
exit /b 0

:fail
echo.
echo [发布失败] 请检查以上报错。
pause
exit /b 1
