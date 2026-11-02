@echo off
rem Installs Subtitle Maker on this PC (or repairs or updates it). Just double-click it.
rem It downloads install.ps1 from GitHub and runs it. Pasting this into PowerShell does the same
rem with no download warnings at all:
rem     irm https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1 ^| iex
rem
rem It saves the script to a file and runs that file, rather than running downloaded text straight
rem from memory: antivirus treats that "download and run" one-liner in a .bat as a sign of malware.
title Installing Subtitle Maker
setlocal
set "SCRIPT=%TEMP%\subtitle-maker-install.ps1"
set "URL=https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1"
if exist "%SCRIPT%" del /f /q "%SCRIPT%" >nul 2>&1
echo Downloading the installer...
curl.exe -fsSL --retry 3 -o "%SCRIPT%" "%URL%" >nul 2>&1
if not exist "%SCRIPT%" powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = 'Tls12'; Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -OutFile '%SCRIPT%'"
if not exist "%SCRIPT%" goto :failed
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { . '%SCRIPT%'; if (-not $global:SubtitleMakerInstalled) { exit 1 } }"
if errorlevel 1 goto :failed
del /f /q "%SCRIPT%" >nul 2>&1
echo.
pause
exit /b 0

:failed
del /f /q "%SCRIPT%" >nul 2>&1
echo.
echo The install did not finish. Check your internet connection and run this file again.
pause
exit /b 1
