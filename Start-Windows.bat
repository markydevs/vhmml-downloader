@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 bootstrap.py %*
  goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
  python bootstrap.py %*
  goto finished
)
echo Please install Python 3.11 or newer from https://www.python.org/downloads/
echo Include Tcl/Tk and select Add Python to PATH if offered.
pause
exit /b 1
:finished
if errorlevel 1 (
  pause
  exit /b 1
)
exit /b 0
