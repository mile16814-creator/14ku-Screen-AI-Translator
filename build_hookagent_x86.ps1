param(
  [string]$Py32 = "C:\\Users\\13269\\AppData\\Local\\Programs\\Python\\Python314-32\\python.exe",
  [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path -LiteralPath $Py32)) {
  throw "Python not found: ${Py32}"
}

function Ensure-Modules {
  param(
    [Parameter(Mandatory = $true)][string]$PythonExe
  )

  $oldEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $PythonExe -m pip -q install -U pip 2>&1 | Out-Host
    & $PythonExe -m pip -q install -U pyinstaller frida 2>&1 | Out-Host
  } finally {
    $ErrorActionPreference = $oldEap
  }

  $oldEap2 = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $PythonExe -c "import frida; print('OK')" 2>&1 | Out-Host
  } finally {
    $ErrorActionPreference = $oldEap2
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Missing module(s) in ${PythonExe}: frida is required."
  }
}

if ($Clean) {
  $paths = @(
    (Join-Path $root "build\\HookAgent-x86"),
    (Join-Path $root "dist\\ScreenTranslator-x86")
  )
  foreach ($p in $paths) {
    if (Test-Path -LiteralPath $p) {
      Remove-Item -LiteralPath $p -Recurse -Force
      Write-Host "Cleaned: $p"
    }
  }
}

$distX86 = Join-Path $root "dist\\ScreenTranslator-x86"
$workX86 = Join-Path $root "build\\HookAgent-x86"

# Always clean dist output to avoid stale files
if (Test-Path -LiteralPath $distX86) {
    Remove-Item -LiteralPath $distX86 -Recurse -Force
    Write-Host "Forced clean of dist: $distX86"
}

Ensure-Modules -PythonExe $Py32

& $Py32 -m PyInstaller --noconfirm --clean --distpath $distX86 --workpath $workX86 "HookAgent.spec"
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed: HookAgent.spec"
}

$exe = Join-Path $root "dist\\ScreenTranslator-x86\\HookAgent\\HookAgent.exe"

# Copy to the nested directory where the main app looks for it
$nestedDest = Join-Path $root "dist\\ScreenTranslator-x64\\ScreenTranslator-x86"
if (Test-Path -LiteralPath $nestedDest) {
    Write-Host "Copying build to nested directory: $nestedDest"
    $dst = Join-Path $root "dist\\ScreenTranslator-x64"
    $ok = $false
    for ($i = 0; $i -lt 6; $i++) {
        try {
            Copy-Item -LiteralPath $distX86 -Destination $dst -Recurse -Force -ErrorAction Stop
            $ok = $true
            break
        } catch {
            Start-Sleep -Milliseconds 350
        }
    }
    if (-not $ok) {
        Write-Warning "Copy to nested directory failed (file in use). Close running HookAgent and rerun."
    }
}

if (Test-Path -LiteralPath $exe) {
  $exe
}
