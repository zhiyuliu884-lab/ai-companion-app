# 可选：Windows 自愈脚本
# 用法：改下面 $TARGETS，注册计划任务每 2 分钟跑一次
$ErrorActionPreference = "Continue"
$log = Join-Path $PSScriptRoot "watchdog.log"

$PY      = "C:\path\to\venv\Scripts\python.exe"
$APPDIR  = "C:\path\to\ai-companion-app\server"
$PORT    = 6191
$TUNNEL  = "C:\path\to\cloudflared.exe"   # 不用外网可留空

function Log($m) { Add-Content $log ("[{0}] {1}" -f (Get-Date -Format "MM-dd HH:mm"), $m) -Encoding UTF8 }
function Alive($u) { try { (Invoke-WebRequest $u -TimeoutSec 6 -UseBasicParsing).StatusCode -eq 200 } catch { $false } }

if (-not (Alive "http://127.0.0.1:$PORT/api/health")) {
    Log "后端挂了，重启"
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*ai-companion-app*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
    Start-Process $PY -ArgumentList "-u", "$APPDIR\app.py" -WorkingDirectory $APPDIR -WindowStyle Hidden `
        -RedirectStandardOutput "$APPDIR\..\data\app.log" -RedirectStandardError "$APPDIR\..\data\app.err.log"
}

if ($TUNNEL -and -not (Get-CimInstance Win32_Process | Where-Object { $_.Name -like "*cloudflared*" })) {
    Log "隧道挂了，重启"
    Start-Process $TUNNEL -ArgumentList "tunnel", "--url", "http://127.0.0.1:$PORT", "--no-autoupdate" `
        -WindowStyle Hidden -RedirectStandardOutput "$APPDIR\..\data\cf.log" -RedirectStandardError "$APPDIR\..\data\cf.err.log"
}
