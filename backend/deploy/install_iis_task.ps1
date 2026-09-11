# =====================================================================
#  QianFanSync IIS 部署：注册「交互式」计划任务启动后端
#  关键点：任务必须"仅在用户登录时运行"(onlogon + /it)，后端才在桌面会话，
#  浏览器登录（添加账号）才能弹出窗口。
#
#  用法（管理员 PowerShell）：
#    .\install_iis_task.ps1                                  # 安装/更新任务
#    .\install_iis_task.ps1 -SiteRoot "D:\wwwroot\Upload.API" # 指定站点目录
#    .\install_iis_task.ps1 -Uninstall                        # 卸载任务
#
#  说明：
#   - 任务名为 QianFanSyncUploadAPI
#   - 触发方式：用户登录时（onlogon），以交互方式运行（/it）
#   - 若曾以"不管用户是否登录都运行"方式注册（Session 0），本脚本会自动覆盖修正
# =====================================================================
param(
    [string]$SiteRoot = "D:\wwwroot\Upload.API",
    [int]$Port = 6605,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "QianFanSyncUploadAPI"
$RunBat = Join-Path $SiteRoot "run_api.bat"

if ($Uninstall) {
    Write-Host "[install_iis_task] 卸载任务 $TaskName ..."
    schtasks /Delete /TN $TaskName /F | Out-Null
    Write-Host "[install_iis_task] 已卸载。如需恢复后端，请重新运行本脚本（不带 -Uninstall）。"
    exit 0
}

# ---------- 检查 ----------
if (-not (Test-Path $RunBat)) {
    Write-Host "[install_iis_task] 错误：找不到 $RunBat" -ForegroundColor Red
    Write-Host "[install_iis_task] 请先把 backend 发布内容（含 deploy\run_api.bat）拷到 $SiteRoot" -ForegroundColor Yellow
    exit 1
}

# ---------- 删除旧任务（可能是 Session 0 的 Background 模式） ----------
$exists = schtasks /Query /TN $TaskName 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[install_iis_task] 发现旧任务，先删除（旧任务可能运行在 Session 0）..."
    schtasks /Delete /TN $TaskName /F | Out-Null
}

# ---------- 注册交互式任务 ----------
# /sc onlogon ：用户登录时触发
# /ru Administrator ：以管理员用户运行
# /it ：交互式，仅当用户已登录时在用户会话运行（Session > 0）——关键！
$runCmd = "cmd.exe /c `"$RunBat`""
Write-Host "[install_iis_task] 注册交互式计划任务 $TaskName ..."
Write-Host "[install_iis_task] 命令: $runCmd"
schtasks /Create /TN $TaskName /TR $runCmd /SC ONLOGON /RU Administrator /IT /F
if ($LASTEXITCODE -ne 0) {
    Write-Host "[install_iis_task] 注册失败，请以管理员身份运行 PowerShell" -ForegroundColor Red
    exit 1
}

# ---------- 立即手动运行一次（当前用户已登录，会进入桌面会话） ----------
Write-Host "[install_iis_task] 立即启动后端（用户会话）..."
schtasks /Run /TN $TaskName | Out-Null
Start-Sleep -Seconds 3

# ---------- 验证端口 ----------
$portOk = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 2
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        $portOk = $true
        break
    }
}
if ($portOk) {
    $proc = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess
    $session = (Get-CimInstance Win32_Process -Filter "ProcessId=$proc").SessionId
    if ($session -eq 0) {
        Write-Host "[install_iis_task] 警告：端口 $Port 已监听，但进程在 Session 0（浏览器登录仍不可用）。请检查计划任务设置。" -ForegroundColor Red
    } else {
        Write-Host "[install_iis_task] 完成！后端已在端口 $Port 运行（会话 $session，浏览器自动化可用）。" -ForegroundColor Green
    }
} else {
    Write-Host "[install_iis_task] 端口 $Port 未在 40s 内就绪，请查看后端日志 stdout_last.log / stderr_last.log" -ForegroundColor Yellow
}
