<#
.SYNOPSIS
    Sets up the EU Translator on this PC: the Python environment for the API, and the
    .NET 5 SDK + build of the desktop app.

.DESCRIPTION
    Python: creates TranslatorService\.venv and installs requirements.txt (with -Training also
    requirements-train.txt). With -Gpu it also creates TranslatorService\.venv-gpu with a CUDA
    build of PyTorch for training on an NVIDIA GPU.

    .NET: the desktop app targets .NET 5 and is built with the .NET 5 SDK (5.0.408, pinned in
    global.json). If that SDK is not installed on the PC, it is downloaded from Microsoft
    (builds.dotnet.microsoft.com), its SHA-512 hash is verified, and it is unpacked into the
    .dotnet\ folder of this repository. No administrator rights are needed and the PC's own
    .NET installations are not changed. The app is then built and can be started on the
    .NET 5 runtime with run_desktop.cmd.

    Running it again is safe: finished steps are skipped.

    setup.cmd accepts the same options written as --proxy, --training, ...

.EXAMPLE
    .\setup.ps1
.EXAMPLE
    .\setup.ps1 -Proxy http://proxy.yourcompany.com:8080
.EXAMPLE
    .\setup.ps1 -Training -Gpu
.EXAMPLE
    .\setup.ps1 -Wheels D:\wheels -SkipDotnet
#>
[CmdletBinding()]
param(
    # Proxy for all downloads, e.g. http://proxy.corp:8080 or http://user:pass@proxy.corp:8080
    [string]$Proxy = "",
    # Log in to the proxy with your Windows account (for proxies that use Windows sign-in)
    [switch]$ProxyUseDefaultCredentials,
    # Also install the packages for the training scripts (sacrebleu, truststore)
    [switch]$Training,
    # Also create .venv-gpu with PyTorch for NVIDIA GPUs (CUDA 12.6), for training only
    [switch]$Gpu,
    # Install Python packages from this folder of wheel files instead of from pypi.org
    [string]$Wheels = "",
    # Run the Python test suite (pytest) at the end
    [switch]$RunTests,
    # Recreate TranslatorService\.venv even if it exists
    [switch]$Recreate,
    [switch]$SkipPython,
    [switch]$SkipDotnet
)

# "Continue": in Windows PowerShell 5.1, "Stop" turns any stderr output of pip/python/dotnet into
# a fatal error. Failures are checked explicitly instead (exit codes, -ErrorAction Stop).
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"     # the progress bar makes downloads very slow in PowerShell 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = $PSScriptRoot
$Service = Join-Path $Root "TranslatorService"
$Venv = Join-Path $Service ".venv"
$VenvGpu = Join-Path $Service ".venv-gpu"

$DotnetSdkVersion = "5.0.408"                  # last .NET 5 SDK, includes runtime 5.0.17
$DotnetZipUrl = "https://builds.dotnet.microsoft.com/dotnet/Sdk/$DotnetSdkVersion/dotnet-sdk-$DotnetSdkVersion-win-x64.zip"
# SHA-512 from https://builds.dotnet.microsoft.com/dotnet/release-metadata/5.0/releases.json
$DotnetZipSha512 = "3845485401695b325d9afee67e33c6b3a45902476e408dd74ebc8815ad2c4f4b5d70a6b993e87ff587d0d9b0e5a3d66eaf3dd6bf715b0012ffee70501a716485"
$DotnetDir = Join-Path $Root ".dotnet"
$AppExe = Join-Path $Root "TranslatorDesktop\bin\Debug\net5.0-windows\TranslatorDesktop.exe"

$TorchCudaIndex = "https://download.pytorch.org/whl/cu126"

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Info($text) { Write-Host "   $text" }
function Ok($text) { Write-Host "   OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   !   $text" -ForegroundColor Yellow }
function Fail($text) {
    Write-Host ""
    Write-Host "SETUP FAILED: $text" -ForegroundColor Red
    Write-Host "Run .\check_network.cmd to see whether a download site is blocked (add --proxy if you use one)."
    exit 1
}

# Runs a program and stops the setup if it fails
function Run([string]$exe, $arguments, [string]$what) {
    $arguments = @($arguments | Where-Object { $_ })     # drop empty entries
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Fail "$what (exit code $LASTEXITCODE)" }
}

Write-Host "EU Translator setup" -ForegroundColor Cyan
Write-Host "Folder: $Root"

if ($Proxy) {
    # pip, git, dotnet and training\download_data.py all use these
    $env:HTTPS_PROXY = $Proxy
    $env:HTTP_PROXY = $Proxy
    Info "Proxy: $Proxy"
}
if ($Wheels) {
    $Wheels = (Resolve-Path $Wheels -ErrorAction SilentlyContinue).Path
    if (-not $Wheels) { Fail "wheel folder not found" }
    Info "Python packages from: $Wheels (offline)"
}

# ------------------------------------------------------------------------------------------
# Python
# ------------------------------------------------------------------------------------------

function Find-Python {
    # Returns @(exe, args...) for a Python 3.11+ that is not the Microsoft Store placeholder
    $candidates = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") { $candidates += , @($cmd.Source) }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { $candidates += , @($py.Source, "-3") }
    foreach ($c in $candidates) {
        $exe = $c[0]
        $pre = @($c | Select-Object -Skip 1)
        $out = & $exe @pre -c "import sys; print('%d.%d.%d' % sys.version_info[:3]); print(sys.version_info >= (3, 11))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out -and $out[-1] -eq "True") {
            return , @($c + @($out[0]))
        }
    }
    return $null
}

function Get-PipArgs {
    $a = @()
    if ($Wheels) { $a += @("--no-index", "--find-links", $Wheels) }
    elseif ($Proxy) { $a += @("--proxy", $Proxy) }
    return $a
}

function Install-PythonEnv {
    Step "Python environment for the API (TranslatorService\.venv)"
    $venvPython = Join-Path $Venv "Scripts\python.exe"

    if ($Recreate -and (Test-Path $Venv)) {
        Info "Removing the old .venv (-Recreate)"
        Remove-Item $Venv -Recurse -Force -ErrorAction Stop
    }
    $usable = $false
    if (Test-Path $venvPython) {
        & $venvPython -c "import sys" 2>$null
        $usable = ($LASTEXITCODE -eq 0)
        if (-not $usable) {
            Warn ".venv exists but does not work (Python moved or uninstalled); recreating it"
            Remove-Item $Venv -Recurse -Force -ErrorAction Stop
        }
    }

    if (-not $usable) {
        $found = Find-Python
        if (-not $found) {
            Fail ("Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ " +
                  "(tick 'Add python.exe to PATH'), open a new window and run setup again.")
        }
        $exe = $found[0]
        $pre = @($found | Select-Object -Skip 1 -First ($found.Count - 2))
        Info "Using Python $($found[-1]): $exe"
        Run $exe ($pre + @("-m", "venv", $Venv)) "creating the virtual environment"
        Ok "created .venv"
    } else {
        $v = & $venvPython -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
        Ok ".venv already exists (Python $v)"
    }

    if (-not $Wheels) {
        $upgrade = @("-m", "pip", "install", "--upgrade", "pip", "--disable-pip-version-check", "-q") + @(Get-PipArgs | Where-Object { $_ })
        & $venvPython @upgrade
        if ($LASTEXITCODE -ne 0) { Warn "could not upgrade pip; continuing with the installed version" }
    }

    Info "Installing requirements.txt (PyTorch is about 200 MB; this can take a few minutes)"
    Run $venvPython (@("-m", "pip", "install", "--disable-pip-version-check", "-r", (Join-Path $Service "requirements.txt")) + (Get-PipArgs)) "installing requirements.txt"
    if ($Training) {
        Info "Installing requirements-train.txt"
        Run $venvPython (@("-m", "pip", "install", "--disable-pip-version-check", "-r", (Join-Path $Service "requirements-train.txt")) + (Get-PipArgs)) "installing requirements-train.txt"
    }

    $check = "import fastapi, uvicorn, torch, sentencepiece, numpy, httpx; print('torch', torch.__version__)"
    $out = & $venvPython -c $check
    if ($LASTEXITCODE -ne 0) { Fail "the installed packages cannot be imported" }
    Ok "packages work ($out)"

    foreach ($m in @("models\nmt-en-es-fr\model.pt", "models\nmt-en-es-fr\spm.model", "models\lid\lid.pt")) {
        if (-not (Test-Path (Join-Path $Service $m))) { Warn "model file missing: TranslatorService\$m (get it from git, or train it)" }
    }
}

function Install-GpuEnv {
    Step "GPU training environment (TranslatorService\.venv-gpu)"
    $gpuPython = Join-Path $VenvGpu "Scripts\python.exe"
    if (-not (Test-Path $gpuPython)) {
        $found = Find-Python
        if (-not $found) { Fail "Python 3.11 or newer was not found" }
        $pre = @($found | Select-Object -Skip 1 -First ($found.Count - 2))
        Run $found[0] ($pre + @("-m", "venv", $VenvGpu)) "creating .venv-gpu"
        Ok "created .venv-gpu"
    } else {
        Ok ".venv-gpu already exists"
    }
    $pipArgs = @()
    if ($Proxy) { $pipArgs = @("--proxy", $Proxy) }
    if ($Wheels) {
        Run $gpuPython (@("-m", "pip", "install", "--disable-pip-version-check", "--no-index", "--find-links", $Wheels, "torch", "sentencepiece", "numpy", "sacrebleu", "truststore")) "installing GPU packages from the wheel folder"
    } else {
        Info "Installing PyTorch with CUDA 12.6 (about 2.5 GB download)"
        Run $gpuPython (@("-m", "pip", "install", "--disable-pip-version-check", "torch", "--index-url", $TorchCudaIndex) + $pipArgs) "installing CUDA PyTorch"
        Run $gpuPython (@("-m", "pip", "install", "--disable-pip-version-check", "sentencepiece", "numpy", "sacrebleu", "truststore") + $pipArgs) "installing training packages"
    }
    $out = & $gpuPython -c "import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none')"
    if ($out -eq "none") { Warn "PyTorch cannot see a CUDA GPU (no NVIDIA GPU or driver). Training will use the CPU." }
    else { Ok "GPU found: $out" }
}

# ------------------------------------------------------------------------------------------
# .NET 5
# ------------------------------------------------------------------------------------------

function Get-DotnetExe {
    # The SDK in .dotnet\ (installed by this script) first, then a .NET 5 SDK installed on the PC
    $local = Join-Path $DotnetDir "dotnet.exe"
    if ((Test-Path $local) -and (Test-Path (Join-Path $DotnetDir "sdk\$DotnetSdkVersion"))) { return $local }
    $global = Get-Command dotnet -ErrorAction SilentlyContinue
    if ($global) {
        $sdks = & $global.Source --list-sdks 2>$null
        if ($sdks -match "^$([regex]::Escape($DotnetSdkVersion)) ") { return $global.Source }
    }
    return $null
}

function Install-DotnetSdk {
    if ($DotnetDir.Length -gt 90) {
        Warn "the folder path is long ($($DotnetDir.Length) characters); unpacking may hit the Windows 260-character path limit. Move the repository to a shorter path (e.g. D:\Translator) if it fails."
    }
    $zip = Join-Path $env:TEMP "dotnet-sdk-$DotnetSdkVersion-win-x64.zip"
    $haveZip = $false
    if (Test-Path $zip) {
        $haveZip = ((Get-FileHash $zip -Algorithm SHA512).Hash -eq $DotnetZipSha512.ToUpper())
    }
    if (-not $haveZip) {
        Info "Downloading the .NET $DotnetSdkVersion SDK (about 190 MB) from builds.dotnet.microsoft.com"
        $iwr = @{ Uri = $DotnetZipUrl; OutFile = $zip; UseBasicParsing = $true }
        if ($Proxy) { $iwr.Proxy = $Proxy }
        if ($ProxyUseDefaultCredentials) { $iwr.ProxyUseDefaultCredentials = $true }
        try { Invoke-WebRequest @iwr -ErrorAction Stop }
        catch { Fail "could not download $DotnetZipUrl : $($_.Exception.Message)" }
        $hash = (Get-FileHash $zip -Algorithm SHA512).Hash
        if ($hash -ne $DotnetZipSha512.ToUpper()) {
            Remove-Item $zip -Force
            Fail "the downloaded .NET SDK does not match Microsoft's published SHA-512 hash (download corrupted or replaced by a proxy)"
        }
        Ok "downloaded, SHA-512 verified"
    } else {
        Ok "using the already downloaded $zip (SHA-512 verified)"
    }

    if (Test-Path $DotnetDir) { Remove-Item $DotnetDir -Recurse -Force -ErrorAction Stop }   # incomplete earlier attempt
    Info "Unpacking into .dotnet\"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try { [IO.Compression.ZipFile]::ExtractToDirectory($zip, $DotnetDir) }
    catch { Fail "could not unpack the .NET SDK: $($_.Exception.Message)" }
    Remove-Item $zip -Force
    Ok "installed .NET SDK $DotnetSdkVersion (runtime 5.0.17) in .dotnet\"
}

function Install-DesktopApp {
    Step ".NET 5 SDK and desktop app build"
    $dotnet = Get-DotnetExe
    if (-not $dotnet) { Install-DotnetSdk; $dotnet = Get-DotnetExe }
    else { Ok ".NET 5 SDK found: $dotnet" }
    if (-not $dotnet) { Fail ".NET 5 SDK installation did not work" }

    if ($dotnet.StartsWith($DotnetDir)) {
        # Use only the SDK and runtimes in .dotnet\, not the ones installed on the PC
        $env:DOTNET_ROOT = $DotnetDir
        $env:DOTNET_MULTILEVEL_LOOKUP = "0"
    }
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
    $env:DOTNET_NOLOGO = "1"
    $env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = "1"

    $version = & $dotnet --version
    Info "SDK used for the build: $version (selected by global.json)"

    $running = Get-Process TranslatorDesktop -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $AppExe }
    if ($running) { Fail "the desktop app is running from the build folder. Close it and run setup again." }

    Push-Location $Root
    try { Run $dotnet @("build", "TranslatorDesktop", "-c", "Debug", "--nologo", "-v", "minimal") "building the desktop app" }
    finally { Pop-Location }
    if (-not (Test-Path $AppExe)) { Fail "the build finished but $AppExe is missing" }
    Ok "built $AppExe"
}

# ------------------------------------------------------------------------------------------

if (-not $SkipPython) {
    Install-PythonEnv
    if ($Gpu) { Install-GpuEnv }
}
if (-not $SkipDotnet) { Install-DesktopApp }

if ($RunTests -and -not $SkipPython) {
    Step "Python tests"
    Push-Location $Service
    try { Run (Join-Path $Venv "Scripts\python.exe") @("-m", "pytest", "-q") "the Python tests" }
    finally { Pop-Location }
}

Write-Host ""
Write-Host "Setup finished." -ForegroundColor Green
Write-Host ""
Write-Host "Start the translator:"
Write-Host "  1. API, in its own window:   TranslatorService\start_cluster.bat 4"
Write-Host "  2. Desktop app (.NET 5):     .\run_desktop.cmd"
Write-Host ""
Write-Host "Full guide: docs\SETUP_GUIDE.md"
exit 0
