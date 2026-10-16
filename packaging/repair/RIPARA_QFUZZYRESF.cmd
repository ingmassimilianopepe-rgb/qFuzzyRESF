@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0RIPARA_QFUZZYRESF.ps1" %*
set "QFUZZYRESF_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %QFUZZYRESF_EXIT%
