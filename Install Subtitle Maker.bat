@echo off
rem Installs Subtitle Maker on this PC (or repairs or updates it). Just double-click it.
rem It runs install.ps1 from GitHub. Pasting this into PowerShell does the same with no
rem download warnings at all:
rem     irm https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1 ^| iex
title Installing Subtitle Maker
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-Expression (Invoke-RestMethod 'https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1' -UseBasicParsing) } catch { Write-Host $_.Exception.Message -ForegroundColor Red }; if (-not $global:SubtitleMakerInstalled) { exit 1 }"
if errorlevel 1 (
    echo.
    echo The install did not finish. Check your internet connection and run this file again.
    pause
    exit /b 1
)
timeout /t 5 >nul
