@echo off
rem Installs Subtitle Maker on this PC (or repairs or updates it). Just double-click it.
rem It downloads install.ps1 from GitHub and runs it in the Veyra installer window, with no
rem console. Pasting this into PowerShell does the same, in the console instead:
rem     irm https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1 ^| iex
rem
rem It saves the script to a file and runs that file, rather than running downloaded text straight
rem from memory: antivirus treats that "download and run" one-liner in a .bat as a sign of malware.
title Installing Subtitle Maker
setlocal
set "SCRIPT=%TEMP%\subtitle-maker-install.ps1"
set "URL=https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1"
if defined SUBTITLE_MAKER_INSTALLER_URL set "URL=%SUBTITLE_MAKER_INSTALLER_URL%"
if exist "%SCRIPT%" del /f /q "%SCRIPT%" >nul 2>&1
echo Opening the Subtitle Maker installer...
curl.exe -fsSL --retry 3 -o "%SCRIPT%" "%URL%" >nul 2>&1
if not exist "%SCRIPT%" powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = 'Tls12'; Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -OutFile '%SCRIPT%'" >nul 2>&1
if not exist "%SCRIPT%" goto :failed
rem The installer window takes it from here; this console closes straight away
start "" powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "$env:SUBTITLE_MAKER_GUI = '1'; . '%SCRIPT%'"
exit /b 0

:failed
echo.
echo Couldn't download the installer. Check your internet connection and run this file again.
pause
exit /b 1
