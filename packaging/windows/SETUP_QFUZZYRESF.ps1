# Compatible with Windows PowerShell 5.1 and PowerShell 7.
[CmdletBinding()]
param([string]$PythonExecutable = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PackageRoot = $PSScriptRoot
$Probe = Join-Path $PackageRoot "verify_runtime.py"
$Backend = Join-Path $PackageRoot "CloudCompare\qFuzzyRESF\backend"
$EnvironmentRoot = Join-Path $PackageRoot ".venv"
$EnvironmentPython = Join-Path $EnvironmentRoot "Scripts\python.exe"
$TranscriptStarted = $false
$SetupExitCode = 0

function Get-InterpreterProbe {
    param([string]$Command, [string[]]$LeadingArguments = @())
    try {
        $RawResult = & $Command @LeadingArguments $Probe --mode interpreter 2>&1
        $ProbeExitCode = $LASTEXITCODE
        if ($ProbeExitCode -ne 0) { return $null }
        $TextResult = ($RawResult | ForEach-Object { [string]$_ }) -join "`n"
        $Result = $TextResult | ConvertFrom-Json
        if ($Result.supported -and $Result.bits -eq 64) { return $Result }
    } catch {
        # A missing py version or unusable interpreter is not a selected runtime.
    }
    return $null
}

function Invoke-PythonChecked {
    param([string]$Executable, [string[]]$PythonArguments)
    & $Executable @PythonArguments
    $PythonExitCode = $LASTEXITCODE
    if ($PythonExitCode -ne 0) {
        throw "Python ha restituito il codice $PythonExitCode. Vedere il log e il messaggio precedente."
    }
}

try {
    $LogDirectory = Join-Path $PackageRoot "logs"
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    $LogPath = Join-Path $LogDirectory ("setup-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
    Start-Transcript -Path $LogPath | Out-Null
    $TranscriptStarted = $true
    Write-Host "Configurazione qFuzzyRESF per CloudCompare"
    Write-Host "Cartella: $PackageRoot"
    if (!(Test-Path -LiteralPath $Probe -PathType Leaf)) { throw "verify_runtime.py mancante: estrarre nuovamente l'intero archivio ZIP." }
    if (!(Test-Path -LiteralPath (Join-Path $Backend "requirements.txt") -PathType Leaf)) {
        throw "Backend mancante. Estrarre l'intero archivio, mantenendo le sottocartelle."
    }
    if (!(Test-Path -LiteralPath (Join-Path $PackageRoot "CloudCompare\CloudCompare.exe") -PathType Leaf)) {
        throw "CloudCompare.exe mancante. Questo script richiede il pacchetto Windows completo."
    }

    if (Test-Path -LiteralPath $EnvironmentRoot) {
        if (!(Test-Path -LiteralPath $EnvironmentPython -PathType Leaf)) {
            throw "La cartella .venv esiste ma e' incompleta. Rinominarla e rilanciare SETUP_QFUZZYRESF.cmd."
        }
        $SelectedRuntime = Get-InterpreterProbe -Command $EnvironmentPython
        if ($null -eq $SelectedRuntime) {
            throw "L'ambiente .venv non e' utilizzabile. Rinominarlo e ripetere il setup con Python 3.10-3.12 x64 installato."
        }
        Write-Host "Riutilizzo dell'ambiente isolato esistente."
    } else {
        $SelectedRuntime = $null
        if ($PythonExecutable) {
            $SelectedRuntime = Get-InterpreterProbe -Command $PythonExecutable
            if ($null -eq $SelectedRuntime) { throw "L'interprete specificato non funziona oppure non e' Python 3.10-3.12 a 64 bit: $PythonExecutable" }
        } else {
            $PythonLauncher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($null -ne $PythonLauncher) {
                foreach ($Version in @("-3.12", "-3.11", "-3.10")) {
                    $SelectedRuntime = Get-InterpreterProbe -Command $PythonLauncher.Source -LeadingArguments @($Version)
                    if ($null -ne $SelectedRuntime) { break }
                }
            }
            if ($null -eq $SelectedRuntime) {
                foreach ($CandidateName in @("python.exe", "python3.exe")) {
                    $Candidate = Get-Command $CandidateName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
                    if ($null -ne $Candidate -and $Candidate.Source -notmatch "\\WindowsApps\\") {
                        $SelectedRuntime = Get-InterpreterProbe -Command $Candidate.Source
                        if ($null -ne $SelectedRuntime) { break }
                    }
                }
            }
        }
        if ($null -eq $SelectedRuntime) {
            throw "Python compatibile non trovato. Installare Python 3.12 a 64 bit da python.org (con Python Launcher), quindi ripetere il setup. Sono supportati anche Python 3.10 e 3.11 x64."
        }
        Write-Host ("Python scelto: " + $SelectedRuntime.executable + " (" + ($SelectedRuntime.version -join ".") + ", 64 bit)")
        Invoke-PythonChecked -Executable $SelectedRuntime.executable -PythonArguments @("-m", "venv", $EnvironmentRoot)
    }

    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    Invoke-PythonChecked -Executable $EnvironmentPython -PythonArguments @("-m", "ensurepip", "--upgrade")
    Invoke-PythonChecked -Executable $EnvironmentPython -PythonArguments @("-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:", "-r", (Join-Path $Backend "requirements.txt"))
    Invoke-PythonChecked -Executable $EnvironmentPython -PythonArguments @("-m", "pip", "check")
    Invoke-PythonChecked -Executable $EnvironmentPython -PythonArguments @($Probe, "--mode", "modules", "--backend", $Backend)
    Write-Host ""
    Write-Host "Configurazione completata e dipendenze verificate." -ForegroundColor Green
    Write-Host "Avviare START_QFUZZYRESF.cmd; selezionare una nuvola in CloudCompare e aprire il plugin Fuzzy-RESF."
    Write-Host "Interprete da usare anche nelle impostazioni del plugin: $EnvironmentPython"
    Write-Host "Log: $LogPath"
} catch {
    $SetupExitCode = 1
    [Console]::Error.WriteLine("ERRORE: " + $_.Exception.Message)
    [Console]::Error.WriteLine("Estrarre il pacchetto in una cartella scrivibile, per esempio Documenti\qFuzzyRESF. Non servono diritti di amministratore.")
} finally {
    if ($TranscriptStarted) { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null }
}
exit $SetupExitCode
