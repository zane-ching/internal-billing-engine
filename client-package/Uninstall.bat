@echo off
REM Claude Code usage billing - Windows one-click uninstall.
REM
REM Double-click this file. It removes the hook and only the settings this
REM package added; your own Claude Code settings are left alone, and a
REM timestamped backup is written first.

setlocal
title Claude Code usage billing - uninstall
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo Uninstall finished. You can close this window.
) else (
    echo Uninstall did not complete cleanly ^(exit code %RC%^). See above.
)
echo.
pause
endlocal & exit /b %RC%
