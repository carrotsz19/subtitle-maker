@echo off
rem Opens the Subtitle Maker window. Drop videos onto it, or onto this icon.
if defined SUBTITLE_MAKER_HOME (set "PYW=%SUBTITLE_MAKER_HOME%\venv\Scripts\pythonw.exe") else (set "PYW=%USERPROFILE%\.subtitle-maker\venv\Scripts\pythonw.exe")
if not exist "%PYW%" (
    echo First time on this PC: run Setup.bat in this folder, then try again.
    pause
    exit /b
)
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
start "" "%PYW%" "%~dp0app.py" %*
