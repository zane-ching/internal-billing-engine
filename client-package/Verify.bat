@echo off
REM Claude Code usage billing - Windows one-click check.
REM
REM Double-click this file to re-check that the receiver is reachable and your
REM token is accepted. It writes nothing and changes nothing - run it any time
REM your usage stops showing up.

setlocal
title Claude Code usage billing - verify
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Verify
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo Check passed. You can close this window.
) else (
    echo Check FAILED ^(exit code %RC%^) - until this is fixed, none of your
    echo usage is being recorded. See the reason above.
)
echo.
pause
endlocal & exit /b %RC%
