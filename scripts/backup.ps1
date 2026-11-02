# 可选：每天备份配置和记忆
$ErrorActionPreference = "Continue"
$app   = "C:\path\to\dafeiyu-app"
$dest  = Join-Path $app "backup"
$stamp = Get-Date -Format "yyyyMMdd"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$zip = Join-Path $dest "dafeiyu-$stamp.zip"

$tmp = Join-Path $env:TEMP "dafeiyu_bk_$stamp"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

Copy-Item "$app\config.json" $tmp -ErrorAction SilentlyContinue
Copy-Item "$app\persona.txt" $tmp -ErrorAction SilentlyContinue
Copy-Item "$app\data" $tmp -Recurse -ErrorAction SilentlyContinue

Compress-Archive -Path "$tmp\*" -DestinationPath $zip -Force
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

Get-ChildItem $dest -Filter "dafeiyu-*.zip" | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 7 | Remove-Item -Force
Write-Host "备份完成: $zip"
