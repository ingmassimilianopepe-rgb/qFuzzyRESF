@echo off
setlocal
title Installazione qFuzzyRESF 0.2.0
set "QF_ENV=%LOCALAPPDATA%\qFuzzyRESF\.venv"
set "QF_ROOT=%~dp0.."
if not exist "%QF_ROOT%\backend\requirements.txt" (
    echo Estrarre tutto lo ZIP prima di avviare questo programma.
    goto :failed
)
py -3.12 -m venv "%QF_ENV%"
if errorlevel 1 (
    echo Installare Python 3.12 x64 con il launcher Python e riprovare.
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
echo INSTALLAZIONE COMPLETATA. Interprete per il plugin:
echo %QF_PYTHON%
echo.
pause
exit /b 0
:failed
echo.
echo INSTALLAZIONE NON COMPLETATA. Conservare il messaggio precedente.
pause
exit /b 1
