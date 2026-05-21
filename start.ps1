$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppPath = Join-Path $RootDir "tradingagents\ui\streamlit_app.py"
$Port = if ($env:PORT) { $env:PORT } else { "8501" }

if (-not (Test-Path $AppPath)) {
    Write-Error "Streamlit app not found at $AppPath"
}

python -m streamlit run $AppPath --server.address 0.0.0.0 --server.port $Port

