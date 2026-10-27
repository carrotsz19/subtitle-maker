@echo off
rem Removes Subtitle Maker from this PC (the window can do this too: Settings > Uninstall).
rem Your videos and .srt files are never touched.
if defined SUBTITLE_MAKER_HOME (set "PY=%SUBTITLE_MAKER_HOME%\venv\Scripts\python.exe") else (set "PY=%USERPROFILE%\.subtitle-maker\venv\Scripts\python.exe")
if not exist "%PY%" (
    echo Subtitle Maker isn't set up on this PC, so there is nothing to remove.
    pause
    exit /b
)
"%PY%" "%~dp0uninstall.py"
pause
