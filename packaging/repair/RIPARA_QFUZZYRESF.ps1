# Repair the v8 portable installation without replacing its compiled DLLs.
[CmdletBinding()]
param([string]$InstallPath = "C:\fuzzy")
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try {
    if (!(Test-Path -LiteralPath (Join-Path $InstallPath "CloudCompare\CloudCompare.exe") -PathType Leaf)) {
        $InstallPath = Read-Host "Cartella che contiene CloudCompare e SETUP_QFUZZYRESF.cmd (esempio C:\fuzzy)"
    }
    $InstallPath = (Resolve-Path -LiteralPath $InstallPath).Path
    $Backend = Join-Path $InstallPath "CloudCompare\qFuzzyRESF\backend"
    if (!(Test-Path -LiteralPath (Join-Path $InstallPath "CloudCompare\CloudCompare.exe") -PathType Leaf)) {
        throw "Non e' la cartella del pacchetto qFuzzyRESF. Nessuna modifica eseguita."
    }
    $Pipeline = Join-Path $Backend "semantic_pipeline.py"
    if (!(Test-Path -LiteralPath $Pipeline -PathType Leaf) -or (Get-Content -LiteralPath $Pipeline -Raw) -notmatch '8\.0-stepwise-semantic') {
        throw "Questa correzione richiede la pipeline qFuzzyRESF v8. Nessuna modifica eseguita."
    }
    $Active = Get-Process CloudCompare -ErrorAction SilentlyContinue
    if ($Active) { throw "Chiudere CloudCompare prima di applicare la correzione." }
    $Payload = Join-Path $PSScriptRoot "files"
    $Files = @(Get-ChildItem -LiteralPath $Payload -File -Recurse)
    if ($Files.Count -lt 4) { throw "Archivio della correzione incompleto: estrarre tutto lo ZIP." }
    $Backup = Join-Path $InstallPath ("backup-runtime-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Path $Backup | Out-Null
    foreach ($Source in $Files) {
        $Relative = $Source.FullName.Substring($Payload.Length).TrimStart([char[]]"\/")
        $Target = Join-Path $InstallPath $Relative
        if (Test-Path -LiteralPath $Target -PathType Leaf) {
            $Saved = Join-Path $Backup $Relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $Saved) -Force | Out-Null
            Copy-Item -LiteralPath $Target -Destination $Saved
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force | Out-Null
        Copy-Item -LiteralPath $Source.FullName -Destination $Target -Force
    }
    Write-Host "Avvio installazione/verifica delle dipendenze nell'ambiente Python del pacchetto."
    $Setup = Join-Path $InstallPath "SETUP_QFUZZYRESF.ps1"
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $Setup
    if ($LASTEXITCODE -ne 0) { throw "File aggiornati, ma il setup delle dipendenze non e' riuscito. Consultare i log in $InstallPath\logs e ripetere SETUP_QFUZZYRESF.cmd." }
    Write-Host ""
    Write-Host "CORREZIONE COMPLETATA. Avviare START_QFUZZYRESF.cmd dalla cartella di installazione." -ForegroundColor Green
    Write-Host "Backup dei file precedenti: $Backup"
    exit 0
} catch {
    [Console]::Error.WriteLine("CORREZIONE NON COMPLETATA: " + $_.Exception.Message)
    exit 1
}
