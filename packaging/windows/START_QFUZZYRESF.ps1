# Starts the bundled CloudCompare with the exact isolated Python environment.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try {
    $PackageRoot = $PSScriptRoot
    $ApplicationDirectory = Join-Path $PackageRoot "CloudCompare"
    $Application = Join-Path $ApplicationDirectory "CloudCompare.exe"
    $EnvironmentScripts = Join-Path $PackageRoot ".venv\Scripts"
    $EnvironmentPython = Join-Path $EnvironmentScripts "python.exe"
    $Probe = Join-Path $PackageRoot "verify_runtime.py"
    $Backend = Join-Path $ApplicationDirectory "qFuzzyRESF\backend"
    if (!(Test-Path -LiteralPath $Application -PathType Leaf)) { throw "CloudCompare.exe non trovato. Estrarre l'intero archivio ZIP." }
    if (!(Test-Path -LiteralPath $EnvironmentPython -PathType Leaf)) { throw "Ambiente Python assente. Eseguire prima SETUP_QFUZZYRESF.cmd." }
    if (!(Test-Path -LiteralPath $Probe -PathType Leaf)) { throw "verify_runtime.py mancante. Estrarre l'intero archivio ZIP." }
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    & $EnvironmentPython $Probe --mode modules --backend $Backend
    $ProbeExitCode = $LASTEXITCODE
    if ($ProbeExitCode -ne 0) { throw "La verifica del backend e' fallita (codice $ProbeExitCode). Eseguire SETUP_QFUZZYRESF.cmd e leggere il log." }
    $env:PATH = $EnvironmentScripts + [IO.Path]::PathSeparator + $env:PATH
    $env:QFUZZYRESF_PYTHON = $EnvironmentPython
    $env:QFUZZYRESF_BACKEND_DIR = $Backend
    Start-Process -FilePath $Application -WorkingDirectory $ApplicationDirectory | Out-Null
    Write-Host "CloudCompare avviato con qFuzzyRESF."
    exit 0
} catch {
    [Console]::Error.WriteLine("ERRORE: " + $_.Exception.Message)
    exit 1
}
