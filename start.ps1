#requires -version 5.1
<#
Windows equivalent of start.sh:
- checkout dev + pull
- bootstrap conda/miniconda
- ensure env (Python 3.13)
- install deps
- restart web server on port 8000
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Initialize-Git {
  if (Get-Command git -ErrorAction SilentlyContinue) {
    return
  }

  Write-Host "Git not found; trying to install..."
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id Git.Git --exact --silent --accept-package-agreements --accept-source-agreements | Out-Null
    return
  }
  if (Get-Command choco -ErrorAction SilentlyContinue) {
    choco install git -y | Out-Null
    return
  }
  if (Get-Command scoop -ErrorAction SilentlyContinue) {
    scoop install git | Out-Null
    return
  }

  throw "Could not install git automatically. Install Git first, then rerun."
}

function Test-Health([string]$Url) {
  try {
    $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
    return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300)
  } catch {
    return $false
  }
}

function Stop-ListenersOnPort([int]$Port) {
  try {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
  } catch {
    return
  }
  $pids = @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
  foreach ($processId in $pids) {
    try {
      Stop-Process -Id $processId -Force -ErrorAction Stop
    } catch {
      Write-Warning "Failed to stop PID $processId on port ${Port}: $($_.Exception.Message)"
    }
  }
}

function Install-Miniconda([string]$InstallDir) {
  Write-Host "Installing Miniconda to $InstallDir ..."
  $tmpExe = Join-Path $env:TEMP "Miniconda3-latest-Windows-x86_64.exe"
  Invoke-WebRequest -Uri "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe" -OutFile $tmpExe

  # /D must be the final arg and unquoted for installer parsing.
  $installArgs = "/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=$InstallDir"
  $proc = Start-Process -FilePath $tmpExe -ArgumentList $installArgs -Wait -PassThru
  Remove-Item -Path $tmpExe -Force -ErrorAction SilentlyContinue
  if ($proc.ExitCode -ne 0) {
    throw "Miniconda installer failed with exit code $($proc.ExitCode)"
  }
}

function Initialize-Conda([string]$InstallDir) {
  if (Get-Command conda -ErrorAction SilentlyContinue) {
    $hook = conda "shell.powershell" "hook"
    $hook | Out-String | Invoke-Expression
    return
  }

  $condaExe = Join-Path $InstallDir "Scripts\conda.exe"
  if (-not (Test-Path $condaExe)) {
    Install-Miniconda -InstallDir $InstallDir
  }

  $hook = & $condaExe "shell.powershell" "hook"
  $hook | Out-String | Invoke-Expression
}

function Initialize-CondaTerms {
  $channels = @(
    "https://repo.anaconda.com/pkgs/main",
    "https://repo.anaconda.com/pkgs/r",
    "https://repo.anaconda.com/pkgs/msys2"
  )
  foreach ($channel in $channels) {
    try {
      conda tos accept --override-channels --channel $channel | Out-Null
    } catch {
      # Ignore if already accepted or unsupported conda version.
    }
  }
}

function Test-CondaEnvExists([string]$Name) {
  $envNames = conda env list | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    ($line -split "\s+")[0]
  } | Where-Object { $_ }

  return $envNames -contains $Name
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Initialize-Git
git checkout dev
git pull

$InstallDir = if ($env:MINICONDA_INSTALL_DIR) { $env:MINICONDA_INSTALL_DIR } else { Join-Path $env:USERPROFILE "miniconda3" }
$EnvName = if ($env:CONDA_ENV_NAME) { $env:CONDA_ENV_NAME } else { "tradingagents" }
$Port = 8000
$HealthUrl = "http://127.0.0.1:$Port/healthz"

Initialize-Conda -InstallDir $InstallDir
Initialize-CondaTerms

if (-not (Test-CondaEnvExists -Name $EnvName)) {
  conda create -n $EnvName python=3.13 -y
}

conda activate $EnvName

Set-Location $Root
python -m pip install -U pip
pip install .
pip install ".[web]"

if (Test-Health -Url $HealthUrl) {
  Write-Host "Server already responding on port $Port; stopping it before restart..."
  Stop-ListenersOnPort -Port $Port
  Start-Sleep -Seconds 1
  if (Test-Health -Url $HealthUrl) {
    throw "Still reachable on $HealthUrl; aborting"
  }
}

tradingagents-web
