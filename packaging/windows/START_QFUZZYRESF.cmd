@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_QFUZZYRESF.ps1"
set "QFUZZYRESF_EXIT=%ERRORLEVEL%"
if not "%QFUZZYRESF_EXIT%"=="0" pause
exit /b %QFUZZYRESF_EXIT%
