@echo off
rem Checks GitHub for a newer Subtitle Maker and installs it into this folder.
rem Your Voices clips, settings and models are kept. The window can do this too (Settings > Updates).
if defined SUBTITLE_MAKER_HOME (set "PY=%SUBTITLE_MAKER_HOME%\venv\Scripts\python.exe") else (set "PY=%USERPROFILE%\.subtitle-maker\venv\Scripts\python.exe")
if not exist "%PY%" (
    echo First time on this PC: run Setup.bat in this folder, or "Install Subtitle Maker.bat".
    pause
    exit /b
)
"%PY%" "%~dp0update.py" %*
pause
