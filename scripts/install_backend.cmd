@echo off
setlocal
title qFuzzyRESF 0.3.0 installation
set "QF_ENV=%LOCALAPPDATA%\qFuzzyRESF\.venv"
set "QF_ROOT=%~dp0.."
if not exist "%QF_ROOT%\backend\requirements.txt" (
    echo Extract the complete ZIP before running this installer.
    goto :failed
)
py -3.12 -m venv "%QF_ENV%"
if errorlevel 1 (
    echo Install Python 3.12 x64 with the Python launcher, then try again.
    goto :failed
)
set "QF_PYTHON=%QF_ENV%\Scripts\python.exe"
"%QF_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :failed
"%QF_PYTHON%" -m pip install -r "%QF_ROOT%\backend\requirements.txt"
if errorlevel 1 goto :failed
"%QF_PYTHON%" "%QF_ROOT%\backend\run_pipeline.py" --check
if errorlevel 1 goto :failed
echo.
echo INSTALLATION COMPLETE. Python interpreter for the plugin:
echo %QF_PYTHON%
echo.
pause
exit /b 0
:failed
echo.
echo INSTALLATION FAILED. Keep the preceding error message.
pause
exit /b 1
