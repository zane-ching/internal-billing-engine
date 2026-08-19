@echo off
REM Claude Code usage billing - Windows one-click install.
REM
REM Double-click this file. No arguments, no terminal, no admin rights.
REM Everything it needs (receiver URL and token) is baked into the package.
REM
REM It delegates to install.ps1 -> configure.py, so there is one copy of the
REM install logic and this file stays a launcher. -ExecutionPolicy Bypass is
REM scoped to this one process: it does not change the machine's policy, and it
REM is what stops a default Windows box from refusing the .ps1 outright.
REM
REM -NoProfile skips the user's PowerShell profile, which on a locked-down or
REM heavily customised box is a common source of unrelated startup errors.

setlocal
title Claude Code usage billing - install
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Interactive
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo Install finished. You can close this window.
) else (
    echo Install did not complete cleanly ^(exit code %RC%^).
    echo Read the messages above - they say which step failed and what to do.
)
echo.
pause
endlocal & exit /b %RC%
