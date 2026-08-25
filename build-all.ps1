# ============================================================
# pet-nailong - all-in-one exe build script
# Usage:  .\build-all.ps1            # build exe + zip to project root
# Deps:   vendored _pyinstaller (PyInstaller 6.22.2)
# Output:
#   pet-nailong-all.exe   # single-file windowed exe (double-click to run)
#   pet-nailong-all.zip   # release archive (exe + README)
# ============================================================
param(
  [string]$OutDir = "."
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$outAbs = [System.IO.Path]::GetFullPath((Join-Path $root $OutDir))
$spec = Join-Path $root 'helper\pet_all.spec'
$distExe = Join-Path $root 'dist\pet-nailong-all.exe'
$targetExe = Join-Path $outAbs 'pet-nailong-all.exe'
$zipPath = Join-Path $outAbs 'pet-nailong-all.zip'

# ---- 门禁校验：打包前自动运行 ----
Write-Host "[pet-nailong] Running gate verification ..."
$gateResult = python (Join-Path $root 'scripts\gates\verify.py')
if ($LASTEXITCODE -ne 0) {
  Write-Host "[pet-nailong] Gate verification FAILED. Fix errors before building." -ForegroundColor Red
  exit 1
}
Write-Host "[pet-nailong] Gate verification passed." -ForegroundColor Green

Write-Host "[pet-nailong] PyInstaller building all-in-one exe ..."
$env:PYTHONPATH = Join-Path $root '_pyinstaller'
python -m PyInstaller --noconfirm --distpath (Join-Path $root 'dist') --workpath (Join-Path $root 'build') $spec
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $distExe)) {
  throw "PyInstaller build failed"
}

Copy-Item $distExe $targetExe -Force
$sizeMB = [math]::Round((Get-Item $targetExe).Length/1MB, 2)
Write-Host "[pet-nailong] exe: $targetExe ($sizeMB MB)"

if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
$staging = Join-Path $env:TEMP "pet-nailong-release"
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging | Out-Null
Copy-Item $targetExe (Join-Path $staging 'pet-nailong-all.exe') -Force
Copy-Item (Join-Path $root 'README.md') (Join-Path $staging 'README.md') -Force
Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $zipPath
Remove-Item $staging -Recurse -Force

$zipMB = [math]::Round((Get-Item $zipPath).Length/1MB, 2)
Write-Host "[pet-nailong] zip: $zipPath ($zipMB MB)"
Write-Host "[pet-nailong] Done. Double-click pet-nailong-all.exe to run (no console)."
