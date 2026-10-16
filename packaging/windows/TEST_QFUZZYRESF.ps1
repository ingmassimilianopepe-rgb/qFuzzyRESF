# Optional end-to-end check: invokes the actual plugin inside CloudCompare.
[CmdletBinding()]
param([ValidateRange(30, 300)][int]$TimeoutSeconds = 300)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$TranscriptStarted = $false
$TestExitCode = 0
try {
    $PackageRoot = $PSScriptRoot
    $ApplicationDirectory = Join-Path $PackageRoot "CloudCompare"
    $Application = Join-Path $ApplicationDirectory "CloudCompare.exe"
    $EnvironmentScripts = Join-Path $PackageRoot ".venv\Scripts"
    $EnvironmentPython = Join-Path $EnvironmentScripts "python.exe"
    $Probe = Join-Path $PackageRoot "verify_runtime.py"
    $Backend = Join-Path $ApplicationDirectory "qFuzzyRESF\backend"
    $Generator = Join-Path $PackageRoot "examples\generate_semantic_room.py"
    foreach ($RequiredPath in @($Application, $EnvironmentPython, $Probe, $Generator)) {
        if (!(Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
            throw "File richiesto assente: $RequiredPath. Estrarre il pacchetto completo ed eseguire SETUP_QFUZZYRESF.cmd."
        }
    }
    $RunName = (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + ([Guid]::NewGuid().ToString("N").Substring(0, 8))
    $SmokeDirectory = Join-Path (Join-Path $PackageRoot "smoke-runs") $RunName
    New-Item -ItemType Directory -Path $SmokeDirectory | Out-Null
    $LogPath = Join-Path $SmokeDirectory "test.log"
    Start-Transcript -Path $LogPath | Out-Null
    $TranscriptStarted = $true
    Write-Host "Test del plugin nativo qFuzzyRESF v8 su scena sintetica."
    Write-Host "Risultati e log: $SmokeDirectory"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    & $EnvironmentPython $Probe --mode modules --backend $Backend
    if ($LASTEXITCODE -ne 0) { throw "Verifica del runtime fallita. Ripetere SETUP_QFUZZYRESF.cmd." }

    $FixtureDirectory = Join-Path $SmokeDirectory "fixture"
    & $EnvironmentPython $Generator --output $FixtureDirectory
    if ($LASTEXITCODE -ne 0) { throw "Generazione della scena di test fallita." }
    $FixturePath = Join-Path $FixtureDirectory "room.xyz"
    $NativeOutputRoot = Join-Path $SmokeDirectory "native_runs"
    $StandardOutput = Join-Path $SmokeDirectory "cloudcompare.stdout.log"
    $StandardError = Join-Path $SmokeDirectory "cloudcompare.stderr.log"
    $env:PATH = $EnvironmentScripts + [IO.Path]::PathSeparator + $env:PATH
    $env:QFUZZYRESF_PYTHON = $EnvironmentPython
    $env:QFUZZYRESF_BACKEND_DIR = $Backend
    $env:QFUZZYRESF_OUTPUT_ROOT = $NativeOutputRoot
    # Start-Process joins ArgumentList; quote the file explicitly to retain spaces.
    $NativeArguments = @("-SILENT", "-AUTO_SAVE", "OFF", "-O", ('"' + $FixturePath + '"'), "-FUZZY_RESF")
    $Process = Start-Process -FilePath $Application -ArgumentList $NativeArguments -WorkingDirectory $ApplicationDirectory -RedirectStandardOutput $StandardOutput -RedirectStandardError $StandardError -PassThru
    $null = $Process.Handle
    if (!$Process.WaitForExit($TimeoutSeconds * 1000)) {
        # Only the process tree started by this test is terminated.
        & taskkill.exe /PID $Process.Id /T /F | Out-Null
        throw "Tempo massimo di $TimeoutSeconds secondi superato. Controllare i log CloudCompare."
    }
    $Process.WaitForExit()
    $NativeExitCode = $Process.ExitCode
    if ($NativeExitCode -ne 0) {
        Get-Content -LiteralPath $StandardError -Tail 40 -ErrorAction SilentlyContinue
        Get-Content -LiteralPath $StandardOutput -Tail 40 -ErrorAction SilentlyContinue
        throw "CloudCompare ha restituito il codice $NativeExitCode. Controllare caricamento del plugin e log."
    }
    $ResultFiles = @(Get-ChildItem -LiteralPath $NativeOutputRoot -Filter "result.json" -Recurse -File)
    if ($ResultFiles.Count -ne 1) { throw "Atteso un result.json prodotto dal plugin, trovati: $($ResultFiles.Count)." }
    $Result = Get-Content -LiteralPath $ResultFiles[0].FullName -Raw | ConvertFrom-Json
    if ($Result.status -ne "success" -or $Result.version -notlike "8.*") { throw "Il risultato del backend non segnala successo della pipeline v8." }
    # This invocation deliberately supplies only XYZ, without semantic labels.
    if ($Result.walls -ne 4 -or $Result.spaces -ne 1 -or $Result.slabs -ne 2 -or $Result.openings -ne 0) {
        throw "Conteggi inattesi: muri=$($Result.walls), spazi=$($Result.spaces), lastre=$($Result.slabs), aperture=$($Result.openings). Conservare i log per la diagnosi."
    }
    if (!(Test-Path -LiteralPath $Result.ifc -PathType Leaf)) { throw "Esportazione IFC mancante." }
    $Validation = Get-Content -LiteralPath $Result.ifc_validation_json -Raw | ConvertFrom-Json
    if (!$Validation.schema_valid -or !$Validation.geometry_valid) { throw "Validazione dello schema o della geometria IFC non superata." }
    Write-Host ""
    Write-Host "TEST SUPERATO: caricamento plugin, backend, 4 muri, 1 spazio, 2 lastre, IFC valido." -ForegroundColor Green
    Write-Host "Nessuna porta/finestra e' stata dedotta dalla sola assenza di punti."
    Write-Host "IFC: $($Result.ifc)"
    Write-Host "Questo test verifica il funzionamento tecnico; non misura l'accuratezza su rilievi reali."
} catch {
    $TestExitCode = 1
    [Console]::Error.WriteLine("TEST NON SUPERATO: " + $_.Exception.Message)
} finally {
    if ($TranscriptStarted) { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null }
}
exit $TestExitCode
