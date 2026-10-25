param([string]$EnvironmentPath = (Join-Path $PSScriptRoot '..\.venv'))
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
py -3.12 -m venv $EnvironmentPath
if ($LASTEXITCODE -ne 0) { throw 'Installare Python 3.12 x64 e riprovare.' }
$Python = Join-Path $EnvironmentPath 'Scripts\python.exe'
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Aggiornamento pip fallito.' }
& $Python -m pip install -r (Join-Path $Root 'backend\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installazione dipendenze fallita.' }
& $Python (Join-Path $Root 'backend\run_pipeline.py') --check
if ($LASTEXITCODE -ne 0) { throw 'Verifica dipendenze fallita.' }
Write-Host "Interprete da selezionare nel plugin: $((Resolve-Path $Python).Path)"
