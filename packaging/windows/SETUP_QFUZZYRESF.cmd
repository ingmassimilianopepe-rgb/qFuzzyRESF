@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0SETUP_QFUZZYRESF.ps1" %*
set "QFUZZYRESF_EXIT=%ERRORLEVEL%"
echo.
if not "%QFUZZYRESF_EXIT%"=="0" echo Configurazione non completata. Leggere il messaggio di errore e il log nella cartella logs.
pause
exit /b %QFUZZYRESF_EXIT%
