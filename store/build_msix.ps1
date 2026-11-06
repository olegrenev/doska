# build_msix.ps1 — сборка MSIX-пакета Doska для Microsoft Store.
#
# Использование (в PowerShell, из папки store):
#   .\build_msix.ps1 -PackageName "12345OlegRenev.Doska" `
#       -Publisher "CN=AAAAAAAA-....-...." -PublisherDisplay "Oleg Renev"
#
# Параметры берутся в Partner Center: Apps and games -> Doska ->
# Product identity (Package/Identity/Name и Package/Identity/Publisher).
#
# Что делает скрипт:
#   1. Подставляет значения в AppxManifest.xml;
#   2. Складывает exe и ресурсы в папку компоновки;
#   3. Скачивает makeappx.exe (NuGet Microsoft.Windows.SDK.BuildTools),
#      если Windows SDK не установлен;
#   4. Собирает Doska-3.1.msix;
#   5. (опционально, -Sign) подписывает пакет самоподписанным сертификатом
#      для локальной установки и теста. Для отправки в Store подпись
#      не обязательна: Microsoft переподписывает пакет сам.

param(
    [Parameter(Mandatory=$true)][string]$PackageName,
    [Parameter(Mandatory=$true)][string]$Publisher,
    [string]$PublisherDisplay = "Oleg Renev",
    [switch]$Sign
)

$ErrorActionPreference = "Stop"
$storeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir  = Split-Path -Parent $storeDir
$exeSrc   = Join-Path $rootDir "dist\Doska-3.1.exe"
$stage    = Join-Path $storeDir "_stage"
$outMsix  = Join-Path $storeDir "Doska-3.1.msix"
$sdkDir   = Join-Path $storeDir "_sdk"

if (-not (Test-Path $exeSrc)) { throw "Не найден $exeSrc" }

# 1. папка компоновки
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item $exeSrc (Join-Path $stage "Doska-3.1.exe")
Copy-Item (Join-Path $storeDir "Assets") (Join-Path $stage "Assets") -Recurse

$manifest = Get-Content (Join-Path $storeDir "AppxManifest.xml") -Raw
$manifest = $manifest.Replace("{{PACKAGE_NAME}}", $PackageName)
$manifest = $manifest.Replace("{{PUBLISHER}}", $Publisher)
$manifest = $manifest.Replace("{{PUBLISHER_DISPLAY}}", $PublisherDisplay)
Set-Content (Join-Path $stage "AppxManifest.xml") $manifest -Encoding UTF8

# 2. makeappx: из Windows SDK или из NuGet BuildTools
$makeappx = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin" `
    -Recurse -Filter makeappx.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "x64" } | Select-Object -First 1

if (-not $makeappx) {
    if (-not (Test-Path (Join-Path $sdkDir "makeappx.exe"))) {
        Write-Host "Скачиваю Windows SDK BuildTools с nuget.org ..."
        $nupkg = Join-Path $sdkDir "sdk.nupkg.zip"
        New-Item -ItemType Directory -Path $sdkDir -Force | Out-Null
        Invoke-WebRequest `
            "https://www.nuget.org/api/v2/package/Microsoft.Windows.SDK.BuildTools/10.0.26100.1742" `
            -OutFile $nupkg
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory(
            $nupkg, (Join-Path $sdkDir "pkg"))
        Copy-Item (Join-Path $sdkDir "pkg\bin\10.0.26100.1742\x64\*.exe") $sdkDir
        Copy-Item (Join-Path $sdkDir "pkg\bin\10.0.26100.1742\x64\*.dll") $sdkDir
    }
    $makeappx = Join-Path $sdkDir "makeappx.exe"
    $signtool = Join-Path $sdkDir "signtool.exe"
} else {
    $signtool = Join-Path (Split-Path $makeappx) "signtool.exe"
    $makeappx = $makeappx.FullName
}

# 3. упаковка
Remove-Item $outMsix -Force -ErrorAction SilentlyContinue
& $makeappx pack /d $stage /p $outMsix /nv
if ($LASTEXITCODE -ne 0) { throw "makeappx завершился с кодом $LASTEXITCODE" }
Write-Host "Собрано: $outMsix"

# 4. опциональная подпись для локального теста
if ($Sign) {
    $certName = "DoskaDevTest"
    $cert = Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.Subject -eq "CN=$certName" } | Select-Object -First 1
    if (-not $cert) {
        $cert = New-SelfSignedCertificate -Type Custom `
            -Subject "CN=$certName" `
            -KeyUsage DigitalSignature `
            -FriendlyName "Doska dev test cert" `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3",
                             "2.5.29.19={text}")
    }
    & $signtool sign /fd SHA256 /a /s My /n $certName $outMsix
    if ($LASTEXITCODE -ne 0) { throw "signtool завершился с кодом $LASTEXITCODE" }
    Write-Host "Подписано тестовым сертификатом CN=$certName."
    Write-Host "Для локальной установки импортируйте сертификат в Trusted People:"
    Write-Host "  Export-Cert + Import-Certificate Cert:\LocalMachine\TrustedPeople"
}

Write-Host "Готово. Для Store загружайте этот файл в Partner Center (подпись не нужна)."
