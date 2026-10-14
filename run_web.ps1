# 启动本地 Web UI。
#
# 用这个脚本而不是直接 python -m src.api.app，是因为日志被重定向到文件时
# 需要 UTF-8 编码，否则中文和颜色码会变成乱码。
#
# 用法：
#   .\run_web.ps1                    # http://127.0.0.1:8765
#   .\run_web.ps1 -Port 9000         # 换端口
#   .\run_web.ps1 -NoAccessLog       # 只保留启动日志，不刷每条请求
#   .\run_web.ps1 -Addr 0.0.0.0      # 换监听地址
#   .\run_web.ps1 -Reload            # 改代码后自动重载（开发时常开）
#
# 改了后端代码（src/*.py）一定要重启，否则前端会调用到不存在的接口，
# 表现就是各种「载入失败」「Not Found」——开着 -Reload 可以免掉这一坑。
#
# 注意参数名是 -Addr 而不是 -Host：$Host 是 PowerShell 的内置只读变量
# （表示宿主程序），拿它当参数名会在绑定参数时报
# 「无法覆盖变量 Host，因为它是只读变量或常量」，脚本根本跑不起来。

param(
    [switch]$NoAccessLog,
    [switch]$Reload,
    [int]$Port = 8765,
    [string]$Addr = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

$arguments = @("-m", "src.api.app", "--host", $Addr, "--port", "$Port")
if ($NoAccessLog) { $arguments += "--no-access-log" }
if ($Reload) { $arguments += "--reload" }

Write-Host "启动 Web UI: http://${Addr}:${Port}" -ForegroundColor Cyan
python @arguments
