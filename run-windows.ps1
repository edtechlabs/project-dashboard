$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required but was not found in PATH. Install Git for Windows first."
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    $Python = "py"
    $PythonArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
    $PythonArgs = @()
} else {
    throw "Python 3 was not found."
}

if (-not (Test-Path ".venv" -PathType Container)) {
    & $Python @PythonArgs -m venv .venv
}

$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt
& $VenvPython git_dashboard.py
