param([string]$EnvironmentPath = (Join-Path $env:LOCALAPPDATA 'qFuzzyRESF\.venv'))
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
py -3.12 -m venv $EnvironmentPath
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 x64 and try again.' }
$Python = Join-Path $EnvironmentPath 'Scripts\python.exe'
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip update failed.' }
& $Python -m pip install -r (Join-Path $Root 'backend\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $Python (Join-Path $Root 'backend\run_pipeline.py') --check
if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed.' }
Write-Host "Select this Python interpreter in the plugin: $((Resolve-Path $Python).Path)"
