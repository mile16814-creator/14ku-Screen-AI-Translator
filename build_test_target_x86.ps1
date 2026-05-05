param(
  [string]$Py32 = "C:\Users\13269\AppData\Local\Programs\Python\Python314-32\python.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path -LiteralPath $Py32)) {
  throw "Python not found: ${Py32}"
}

$dist = Join-Path $root "dist\TestTarget-x86"
$work = Join-Path $root "build\TestTarget-x86"
$script = Join-Path $root "tests\hook_test_target.py"

if (Test-Path -LiteralPath $dist) {
    Remove-Item -LiteralPath $dist -Recurse -Force
    Write-Host "Cleaned: $dist"
}

Write-Host "Building 32-bit Test Target..."

& $Py32 -m PyInstaller --noconfirm --clean --distpath $dist --workpath $work --name "TestTarget" --onefile --windowed $script

if ($LASTEXITCODE -eq 0) {
    $exe = Join-Path $dist "TestTarget.exe"
    Write-Host "Build Success: $exe"
    # Write-Host "Launching..."
    # Start-Process $exe
} else {
    Write-Error "Build Failed"
}