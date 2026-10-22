@echo off
rem OCS-in-a-Kit installer launcher for Windows (Command Prompt / double-click).
setlocal
set "DIR=%~dp0"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%DIR%ocs_kit_installer.py" %*
  goto :end
)
where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%DIR%ocs_kit_installer.py" %*
  goto :end
)
echo Python 3.8 or newer is required. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH").
pause
exit /b 1
:end
if not "%ERRORLEVEL%"=="0" pause
exit /b %ERRORLEVEL%
