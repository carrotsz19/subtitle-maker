@echo off
rem Opens the update window: checks GitHub for a newer Subtitle Maker and installs it into this folder.
rem Your Voices clips, settings and models are kept. For a text-only update: Update.bat --console
if defined SUBTITLE_MAKER_HOME (set "PY=%SUBTITLE_MAKER_HOME%\venv\Scripts\python.exe") else (set "PY=%USERPROFILE%\.subtitle-maker\venv\Scripts\python.exe")
if not exist "%PY%" (
    echo First time on this PC: run Setup.bat in this folder, or "Install Subtitle Maker.bat".
    pause
    exit /b
)
if /i "%~1"=="--console" (
    "%PY%" "%~dp0update.py"
    pause
    exit /b
)
start "" "%PY:python.exe=pythonw.exe%" "%~dp0app.py" --update
