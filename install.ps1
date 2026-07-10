$ErrorActionPreference = "Stop"

$pluginName = "ddb-order-downloader"
$version = "0.1.0"
$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$codexHome = Join-Path $env:USERPROFILE ".codex"
$targetRoot = Join-Path $codexHome "plugins\cache\personal\$pluginName\$version"
$configPath = Join-Path $codexHome "config.toml"

if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot ".codex-plugin\plugin.json"))) {
    throw "当前目录不是有效插件目录：缺少 .codex-plugin\plugin.json"
}

if (-not (Test-Path -LiteralPath $codexHome)) {
    New-Item -ItemType Directory -Path $codexHome | Out-Null
}

if (Test-Path -LiteralPath $targetRoot) {
    $backup = "$targetRoot.bak-$(Get-Date -Format yyyyMMddHHmmss)"
    Move-Item -LiteralPath $targetRoot -Destination $backup
    Write-Output "已备份旧插件目录：$backup"
}

New-Item -ItemType Directory -Path (Split-Path -Parent $targetRoot) -Force | Out-Null
Copy-Item -LiteralPath $sourceRoot -Destination $targetRoot -Recurse

if (-not (Test-Path -LiteralPath $configPath)) {
    New-Item -ItemType File -Path $configPath -Force | Out-Null
}

$configText = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8)
$pluginBlock = "[plugins.`"$pluginName@personal`"]`r`nenabled = true"

if ($configText -notmatch "\[plugins\.`"$pluginName@personal`"\]") {
    if ($configText.Length -gt 0 -and -not $configText.EndsWith("`r`n")) {
        $configText += "`r`n"
    }
    $configText += "`r`n$pluginBlock`r`n"
    [System.IO.File]::WriteAllText($configPath, $configText, [System.Text.UTF8Encoding]::new($false))
    Write-Output "已写入 config.toml 插件启用配置。"
} else {
    Write-Output "config.toml 已存在插件配置，未重复写入。"
}

Write-Output "安装完成：$targetRoot"
Write-Output "请完全退出并重新打开 Codex Desktop。"
