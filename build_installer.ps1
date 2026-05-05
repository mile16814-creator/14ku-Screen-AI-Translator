param(
  [string]$Version = "5.0.0",
  [string]$Py32 = "C:\\Users\\13269\\AppData\\Local\\Programs\\Python\\Python314-32\\python.exe",
  [switch]$Clean,
  [switch]$PackageOnly,
  [switch]$BuildOnly,
  [switch]$NoParallel,
  [int]$MaxWorkers = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

try {
  $OutputEncoding = [System.Text.UTF8Encoding]::UTF8
} catch {
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if ([string]::IsNullOrWhiteSpace($Version)) {
  throw "Version is required. Update build_installer.ps1 or pass -Version explicitly."
}

function Resolve-IsccPath {
  if ($env:ISCC_PATH -and (Test-Path -LiteralPath $env:ISCC_PATH)) {
    return $env:ISCC_PATH
  }

  $candidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
  )

  foreach ($p in $candidates) {
    if (Test-Path -LiteralPath $p) {
      return $p
    }
  }

  $cmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Path
  }

  throw "Cannot find Inno Setup compiler ISCC.exe. Install Inno Setup 6 or set ISCC_PATH to ISCC.exe."
}

function Assert-PythonModule {
  param(
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [Parameter(Mandatory = $true)][string]$Module
  )

  if ((-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) -and (-not (Test-Path -LiteralPath $PythonExe))) {
    throw "Python not found: ${PythonExe}"
  }

  $oldEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $PythonExe -c "import $Module; print('OK')" 2>&1 | Out-Host
  } finally {
    $ErrorActionPreference = $oldEap
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Missing module '$Module' in ${PythonExe}."
  }
}

function Ensure-Py32Deps {
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

function Ensure-TypingExtensionsForBuild {
  param(
    [Parameter(Mandatory = $true)][string]$PythonExe
  )

  $constraint = "typing-extensions>=4.12.2,<5.0.0"
  $oldEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $PythonExe -m pip -q install -U $constraint 2>&1 | Out-Host
  } finally {
    $ErrorActionPreference = $oldEap
  }

  $versionOutput = & $PythonExe -c "import importlib.metadata as m; import typing_extensions as te; print(m.version('typing-extensions'))" 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "typing-extensions import failed after upgrade. Details: $versionOutput"
  }

  $versionText = (
    @($versionOutput) |
      ForEach-Object { [string]$_ } |
      Select-Object -Last 1
  ).Trim()
  if (-not $versionText) {
    throw "typing-extensions version check failed: empty output."
  }

  try {
    $versionObj = [version]$versionText
  } catch {
    throw "Cannot parse typing-extensions version: $versionText"
  }

  if ($versionObj -lt [version]"4.12.2") {
    throw "typing-extensions too old: $versionText (need >= 4.12.2)"
  }

  Write-Host "[ 24] typing-extensions: $versionText"
}

function Invoke-PyInstaller {
  param(
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [Parameter(Mandatory = $true)][string]$SpecFile,
    [Parameter(Mandatory = $true)][string]$DistPath,
    [Parameter(Mandatory = $true)][string]$WorkPath
  )

  if ((-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) -and (-not (Test-Path -LiteralPath $PythonExe))) {
    throw "Python not found: ${PythonExe}"
  }

  & $PythonExe -m PyInstaller --noconfirm --clean --distpath $DistPath --workpath $WorkPath $SpecFile
  if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed: ${SpecFile}"
  }
}

function Get-IdleCoreCount {
  $logical = 1
  try {
    $logical = [int]$env:NUMBER_OF_PROCESSORS
  } catch {
    $logical = 1
  }
  if ($logical -lt 1) {
    $logical = 1
  }

  try {
    $cpu = (Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 1 -MaxSamples 1).CounterSamples[0].CookedValue
    $cpu = [Math]::Max(0.0, [Math]::Min(100.0, [double]$cpu))
    $idle = [int][Math]::Floor($logical * (100.0 - $cpu) / 100.0)
    if ($idle -lt 1) {
      $idle = 1
    }
    return $idle
  } catch {
    return $logical
  }
}

function Start-PyInstallerProcess {
  param(
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [Parameter(Mandatory = $true)][string]$SpecFile,
    [Parameter(Mandatory = $true)][string]$DistPath,
    [Parameter(Mandatory = $true)][string]$WorkPath,
    [Parameter(Mandatory = $true)][string]$Tag
  )

  if ((-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) -and (-not (Test-Path -LiteralPath $PythonExe))) {
    throw "Python not found: ${PythonExe}"
  }

  $logDir = Join-Path $root "logs"
  New-Item -ItemType Directory -Path $logDir -Force | Out-Null
  $stdoutLog = Join-Path $logDir ("build_{0}_stdout.log" -f $Tag)
  $stderrLog = Join-Path $logDir ("build_{0}_stderr.log" -f $Tag)
  if (Test-Path -LiteralPath $stdoutLog) { Remove-Item -LiteralPath $stdoutLog -Force }
  if (Test-Path -LiteralPath $stderrLog) { Remove-Item -LiteralPath $stderrLog -Force }

  $args = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--distpath", $DistPath,
    "--workpath", $WorkPath,
    $SpecFile
  )

  $proc = Start-Process -FilePath $PythonExe -ArgumentList $args -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

  return [pscustomobject]@{
    Process = $proc
    Tag = $Tag
    SpecFile = $SpecFile
    StdoutLog = $stdoutLog
    StderrLog = $stderrLog
  }
}

function Wait-PyInstallerProcess {
  param(
    [Parameter(Mandatory = $true)]$Task,
    [string]$ExpectedOutputDir = $null,
    [string]$ExpectedExe = $null
  )

  $Task.Process.WaitForExit()
  $exitCode = $Task.Process.ExitCode

  # When using Start-Process with redirected streams, ExitCode can be null even on success
  $success = ($null -eq $exitCode -or $exitCode -eq 0)
  if (-not $success -and $ExpectedOutputDir -and $ExpectedExe) {
    $exePath = Join-Path $ExpectedOutputDir $ExpectedExe
    if (Test-Path -LiteralPath $exePath) {
      $success = $true
      Write-Host "[ 66] $($Task.Tag): build output exists, treating as success (exit was $exitCode)"
    }
  }
  if (-not $success) {
    $errText = ""
    if (Test-Path -LiteralPath $Task.StderrLog) {
      try {
        $errText = (Get-Content -LiteralPath $Task.StderrLog -Tail 80) -join [Environment]::NewLine
      } catch {
        $errText = ""
      }
    }
    throw "PyInstaller failed: $($Task.SpecFile) [$($Task.Tag)], exit=$exitCode.`n$errText"
  }
}

if ($Clean) {
  try { Write-Progress -Activity "Build installer" -Status "Clean build/dist..." -PercentComplete 2 } catch { }
  Write-Host "[  2] Clean build/dist..."
  $paths = @(
    (Join-Path $root "build"),
    (Join-Path $root "dist"),
    (Join-Path $root "__pycache__")
  )
  foreach ($p in $paths) {
    if (Test-Path -LiteralPath $p) {
      Remove-Item -LiteralPath $p -Recurse -Force
    }
  }
}

$distX64 = Join-Path $root "dist\\ScreenTranslator-x64"
$distX86 = Join-Path $root "dist\\ScreenTranslator-x86"
$workMainX64 = Join-Path $root "build\\ScreenTranslator-x64"
$workHookX64 = Join-Path $root "build\\HookAgent-x64"
$workHookX86 = Join-Path $root "build\\HookAgent-x86"

$idleCores = Get-IdleCoreCount
$parallelWorkers = $idleCores
if ($MaxWorkers -gt 0) {
  $parallelWorkers = [Math]::Min([Math]::Max(1, [int]$MaxWorkers), $idleCores)
}
Write-Host "[ 11] Idle cores: $idleCores, build workers: $parallelWorkers"

if (-not $PackageOnly) {
  try { Write-Progress -Activity "Build installer" -Status "Check Python..." -PercentComplete 10 } catch { }
  Write-Host "[ 10] Check Python..."
  if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    throw "Cannot find python. Install Python 3 and ensure python is in PATH."
  }

  try { Write-Progress -Activity "Build installer" -Status "Fix typing-extensions..." -PercentComplete 20 } catch { }
  Write-Host "[ 20] Fix typing-extensions..."
  Ensure-TypingExtensionsForBuild -PythonExe "python"

  try { Write-Progress -Activity "Build installer" -Status "Build app (PyInstaller x64)..." -PercentComplete 25 } catch { }
  Write-Host "[ 25] Build app (PyInstaller x64)..."
  $distParent = Join-Path $root "dist"
  Invoke-PyInstaller -PythonExe "python" -SpecFile "ScreenTranslator.spec" -DistPath $distParent -WorkPath $workMainX64

  # Ensure torch._export is complete in dist (torch import chain may require torch._export.utils)
  $torchExportDir = Join-Path $distX64 "_internal\torch\_export"
  $torchExportUtils = Join-Path $torchExportDir "utils.py"
  if (-not (Test-Path -LiteralPath $torchExportUtils)) {
    $srcTorchExport = (& python -c "import pathlib, torch; print(pathlib.Path(torch.__file__).parent / '_export')" 2>&1 | Select-Object -Last 1).ToString().Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $srcTorchExport)) {
      throw "Cannot locate torch/_export source directory for fallback copy."
    }

    New-Item -ItemType Directory -Path $torchExportDir -Force | Out-Null
    & robocopy $srcTorchExport $torchExportDir /E /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Host
    if ($LASTEXITCODE -gt 7) {
      throw "Failed to copy torch._export fallback (robocopy exit code: $LASTEXITCODE)"
    }
    Write-Host "[ 26] Filled missing torch._export files: $torchExportDir"
  }

  # Fix transformers TypeVar error on Python 3.13+
  $modelingUtilsPaths = @(
    (Join-Path $distX64 "_internal\transformers\modeling_utils.py")
  )
  foreach ($path in $modelingUtilsPaths) {
    if (Test-Path -LiteralPath $path) {
      $content = Get-Content -LiteralPath $path -Raw -Encoding UTF8
      $old = "        full_annotation = get_type_hints(cls).get(""config"", None)"
      if ($content -and $content.Contains($old)) {
        $new = "        try:`n            full_annotation = get_type_hints(cls).get(""config"", None)`n        except (TypeError, AttributeError):`n            full_annotation = None"
        $content = $content.Replace($old, $new)
        Set-Content -LiteralPath $path -Value $content -Encoding UTF8 -NoNewline
        Write-Host "[ 27] Patched transformers modeling_utils (TypeVar fix): $path"
      }
      break
    }
  }
  $runParallelHooks = (-not $NoParallel) -and ($parallelWorkers -ge 2) -and [bool]$Py32
  if ($runParallelHooks) {
    try { Write-Progress -Activity "Build installer" -Status "Prepare HookAgent x86 deps..." -PercentComplete 55 } catch { }
    Write-Host "[ 55] Prepare HookAgent x86 deps..."
    Ensure-Py32Deps -PythonExe $Py32

    $tmpHookDistX64 = Join-Path $root "dist\\_tmp_hook_x64"
    if (Test-Path -LiteralPath $tmpHookDistX64) {
      Remove-Item -LiteralPath $tmpHookDistX64 -Recurse -Force
    }

    try { Write-Progress -Activity "Build installer" -Status "Build HookAgent x64 + x86 (parallel)..." -PercentComplete 65 } catch { }
    Write-Host "[ 65] Build HookAgent x64 + x86 (parallel)..."

    $taskX64 = Start-PyInstallerProcess -PythonExe "python" -SpecFile "HookAgent.spec" -DistPath $tmpHookDistX64 -WorkPath $workHookX64 -Tag "hook_x64"
    $taskX86 = Start-PyInstallerProcess -PythonExe $Py32 -SpecFile "HookAgent.spec" -DistPath $distX86 -WorkPath $workHookX86 -Tag "hook_x86"

    Wait-PyInstallerProcess -Task $taskX64 -ExpectedOutputDir (Join-Path $tmpHookDistX64 "HookAgent") -ExpectedExe "HookAgent.exe"
    Wait-PyInstallerProcess -Task $taskX86 -ExpectedOutputDir (Join-Path $distX86 "HookAgent") -ExpectedExe "HookAgent.exe"

    $srcHookX64 = Join-Path $tmpHookDistX64 "HookAgent"
    $dstHookX64 = Join-Path $distX64 "HookAgent"
    if (-not (Test-Path -LiteralPath $srcHookX64)) {
      throw "HookAgent x64 output not found: $srcHookX64"
    }
    if (Test-Path -LiteralPath $dstHookX64) {
      Remove-Item -LiteralPath $dstHookX64 -Recurse -Force
    }
    Copy-Item -LiteralPath $srcHookX64 -Destination $dstHookX64 -Recurse -Force
    Write-Host "[ 70] Copied HookAgent x64 -> $dstHookX64"
  } else {
    try { Write-Progress -Activity "Build installer" -Status "Build HookAgent (PyInstaller x64)..." -PercentComplete 45 } catch { }
    Write-Host "[ 45] Build HookAgent (PyInstaller x64)..."
    Invoke-PyInstaller -PythonExe "python" -SpecFile "HookAgent.spec" -DistPath $distX64 -WorkPath $workHookX64

    if ($Py32) {
      try { Write-Progress -Activity "Build installer" -Status "Install x86 HookAgent deps..." -PercentComplete 60 } catch { }
      Write-Host "[ 60] Install x86 HookAgent deps..."
      Ensure-Py32Deps -PythonExe $Py32
      try { Write-Progress -Activity "Build installer" -Status "Build HookAgent (PyInstaller x86)..." -PercentComplete 70 } catch { }
      Write-Host "[ 70] Build HookAgent (PyInstaller x86)..."
      Invoke-PyInstaller -PythonExe $Py32 -SpecFile "HookAgent.spec" -DistPath $distX86 -WorkPath $workHookX86
    }
  }

  # Copy models and tesseract into ScreenTranslator-x64
  $targetDirs = @($distX64)
  $targetDir = $null
  foreach ($d in $targetDirs) {
    if (Test-Path -LiteralPath (Join-Path $d "ScreenTranslator.exe")) {
      $targetDir = $d
      break
    }
    if (Test-Path -LiteralPath (Join-Path $d "_internal")) {
      $targetDir = $d
      break
    }
  }
  if ($targetDir) {
    $modelsSrc = Join-Path $root "models"
    $tesseractSrc = Join-Path $root "tesseract"
    if (Test-Path -LiteralPath $modelsSrc) {
      $modelsDst = Join-Path $targetDir "models"
      if (Test-Path -LiteralPath $modelsDst) { Remove-Item -LiteralPath $modelsDst -Recurse -Force }
      Copy-Item -LiteralPath $modelsSrc -Destination $modelsDst -Recurse -Force
      Write-Host "[ 75] Copied models -> $targetDir"
    }
    if (Test-Path -LiteralPath $tesseractSrc) {
      $tesseractDst = Join-Path $targetDir "tesseract"
      if (Test-Path -LiteralPath $tesseractDst) { Remove-Item -LiteralPath $tesseractDst -Recurse -Force }
      Copy-Item -LiteralPath $tesseractSrc -Destination $tesseractDst -Recurse -Force
      Write-Host "[ 76] Copied tesseract -> $targetDir"
    }
  }
}

if ($BuildOnly) {
  Write-Host "[100] BuildOnly done. Run pack_dist_x64_installer.bat to create installer."
  exit 0
}

$sourceDirAbs = Join-Path $root "dist\\ScreenTranslator-x64"
if (-not (Test-Path -LiteralPath (Join-Path $sourceDirAbs "ScreenTranslator.exe"))) {
  throw "Cannot find dist output. Expected ScreenTranslator.exe in: ${sourceDirAbs}"
}
$sourceDirMacro = "..\\dist\\ScreenTranslator-x64"

try { Write-Progress -Activity "Build installer" -Status "Resolve Inno Setup compiler..." -PercentComplete 80 } catch { }
Write-Host "[ 80] Resolve Inno Setup compiler..."
$iscc = Resolve-IsccPath

try { Write-Progress -Activity "Build installer" -Status "Compile installer (single Setup.exe)..." -PercentComplete 85 } catch { }
Write-Host "[ 85] Compile installer (single Setup.exe)..."
$isccArgs = @()
if ($Version) {
  $isccArgs += "/DMyAppVersion=$Version"
}
$isccArgs += "/DAppArch=x64"
$isccArgs += "/DDualBuild=0"
$isccArgs += "/DSourceDir=$sourceDirMacro"
# LZMA threads: cap at 16 to balance speed vs "Out of memory"
$lzmaThreads = if ($PackageOnly) {
  $n = 1
  try { $n = [Math]::Max(1, [int]$env:NUMBER_OF_PROCESSORS) } catch { }
  [Math]::Min($n, 16)
} else {
  [Math]::Min((Get-IdleCoreCount), 16)
}
if ($MaxWorkers -gt 0) {
  $lzmaThreads = [Math]::Min([Math]::Max(1, [int]$MaxWorkers), $lzmaThreads)
}
Write-Host "[ 84] Inno LZMA threads: $lzmaThreads (capped to avoid OOM)"
$isccArgs += "/DLzmaThreads=$lzmaThreads"
$isccArgs += (Join-Path $root "installer\ScreenTranslator.iss")

& $iscc @isccArgs
if ($LASTEXITCODE -ne 0) {
  throw "Inno Setup compiler failed with exit code: $LASTEXITCODE"
}

try { Write-Progress -Activity "Build installer" -Status "Done" -PercentComplete 100 } catch { }
Write-Host "[100] Done"
try { Write-Progress -Activity "Build installer" -Completed } catch { }

$outDir = Join-Path $root "dist\installer"
if (Test-Path -LiteralPath $outDir) {
  Get-ChildItem -LiteralPath $outDir -Filter "ScreenTranslator_Setup_*.exe" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 5 |
    ForEach-Object { $_.FullName }
}
