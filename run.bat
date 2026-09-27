@echo off
cd /d "%~dp0"
title Portfolio Manager

rem ---- find Python: PATH first, then common Anaconda / Miniconda / python.org locations
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY if exist "%USERPROFILE%\miniconda3\python.exe" set "PY=%USERPROFILE%\miniconda3\python.exe"
if not defined PY if exist "%ProgramData%\anaconda3\python.exe" set "PY=%ProgramData%\anaconda3\python.exe"
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
if not defined PY goto :nopython
echo Using Python: %PY%

if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  "%PY%" -m venv .venv || goto :error
)
set "VPY=.venv\Scripts\python.exe"
echo Installing / checking packages (first run takes a minute)...
"%VPY%" -m pip install -q --disable-pip-version-check -r requirements.txt || goto :error
if not exist .env copy .env.example .env >nul

echo.
echo  Portfolio Manager is running at http://localhost:8000
echo  Keep this window open. Close it (or press Ctrl+C) to stop.
echo.
start "" http://localhost:8000
"%VPY%" -m uvicorn app.main:app --port 8000
goto :eof

:nopython
echo Could not find Python. Install Anaconda or Python 3.10+ and try again.
pause
goto :eof

:error
echo Setup failed - see the messages above.
pause
