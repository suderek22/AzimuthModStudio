$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python -m PyInstaller --noconfirm CarPackStudio.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
$release = Join-Path $PSScriptRoot 'dist\CarPackStudio-0.2.2-beta'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.txt') -Destination $release
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD-PARTY-NOTICES.txt') -Destination $release
$report = Join-Path $PSScriptRoot 'build\release-verification.json'
$env:QT_QPA_PLATFORM = 'offscreen'
$process = Start-Process -FilePath (Join-Path $release 'Car Pack Studio.exe') `
    -ArgumentList @('--verify-release', ('"{0}"' -f $report)) -PassThru -Wait
if ($process.ExitCode -ne 0) { throw "Packaged verification failed; see $report" }
$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
if (-not $result.ok) { throw "Packaged verification failed: $($result.error)" }
Remove-Item Env:\QT_QPA_PLATFORM
$zip = Join-Path $PSScriptRoot 'dist\CarPackStudio-0.2.2-beta-win64.zip'
Compress-Archive -LiteralPath $release -DestinationPath $zip -Force -CompressionLevel Optimal
$hash = Get-FileHash -LiteralPath $zip -Algorithm SHA256
('{0}  {1}' -f $hash.Hash, (Split-Path $zip -Leaf)) | Set-Content `
    -LiteralPath ($zip + '.sha256') -Encoding ASCII
Write-Output "Verified release: $zip"
Write-Output "SHA256: $($hash.Hash)"
