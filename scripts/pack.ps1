# batteryinfoview 打包脚本 —— 产出「plugin.json 位于 zip 根」的发布包（协议 §7.3）。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\pack.ps1 [-OutDir <目录>] [-Version <版本>]
#     OutDir  输出目录（默认 <仓库根>\dist）
#     Version 发布版本（默认取 plugin.json 的 version）
# 输出：<OutDir>\AnalysisBuddy_batteryinfoview_v<version>.zip + 同目录 SHA256SUMS.txt
# 内嵌校验：zip 条目无绝对路径 / `..` 越界；plugin.json 位于 zip 根；zip 内
# manifest 的 id/version 与打包参数一致。
#
# 兼容 PowerShell 5.1；无第三方依赖（Compress-Archive + System.IO.Compression）。

param(
    [string]$OutDir = "",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

# --- 读 manifest（id/version 即打包参数来源）---
$manifestPath = Join-Path $RepoRoot "plugin.json"
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$id = $manifest.id
if ($Version -eq "") { $Version = $manifest.version }

# --- 发布包内容清单（不含 .git/tests/.github/scripts/__pycache__ 等）---
$required = @("plugin.json", "main.py", "parser.py", "config.json", "README.md", "LICENSE")
foreach ($name in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot $name))) {
        throw "missing required file for packaging: $name"
    }
}

if ($OutDir -eq "") { $OutDir = Join-Path $RepoRoot "dist" }
$zipName = "AnalysisBuddy_${id}_v${Version}.zip"
$zipPath = Join-Path $OutDir $zipName
# Compress-Archive 会把传入目录本身作为顶层条目，故先把内容铺平到临时根再压
$stage = Join-Path $env:TEMP ("biv-pack-" + [guid]::NewGuid().ToString("N"))

try {
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    foreach ($name in $required) {
        Copy-Item -LiteralPath (Join-Path $RepoRoot $name) -Destination $stage
    }
    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zipPath -CompressionLevel Optimal

    # --- 内嵌校验（System.IO.Compression 直接查 zip 条目）---
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $entries = @($zip.Entries)
        foreach ($e in $entries) {
            $entryName = $e.FullName -replace "\\", "/"
            if ($entryName -match "^[a-zA-Z]:" -or $entryName.StartsWith("/") -or
                $entryName.Contains("..")) {
                throw "zip entry escapes the archive root: $entryName"
            }
        }
        $rootEntries = @($entries | Where-Object {
                -not $_.FullName.Contains("/") -and -not $_.FullName.EndsWith("/") })
        if (@($rootEntries).FullName -notcontains "plugin.json") {
            throw "plugin.json must be at the zip root"
        }
        # zip 内 manifest 的 id/version 须与打包参数一致
        $inner = $zip.GetEntry("plugin.json")
        $reader = New-Object System.IO.StreamReader($inner.Open())
        try {
            $innerJson = $reader.ReadToEnd() | ConvertFrom-Json
        }
        finally {
            $reader.Dispose()
        }
        if ($innerJson.id -ne $id -or $innerJson.version -ne $Version) {
            throw "manifest id/version mismatch in zip: $($innerJson.id)@$($innerJson.version) vs ${id}@${Version}"
        }
    }
    finally {
        $zip.Dispose()
    }

    # --- SHA256 清单（发布资产，随 Release 上传）---
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
    $sumsPath = Join-Path $OutDir "SHA256SUMS.txt"
    "$hash  $zipName" | Set-Content -LiteralPath $sumsPath -Encoding ascii

    Write-Output "packed: $zipPath"
    Write-Output "sha256: $hash"
    Write-Output "sums:   $sumsPath"
}
finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}
