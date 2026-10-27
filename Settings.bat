@echo off
rem Change the quality level or language, then download the model it needs.
if defined SUBTITLE_MAKER_HOME (set "PY=%SUBTITLE_MAKER_HOME%\venv\Scripts\python.exe") else (set "PY=%USERPROFILE%\.subtitle-maker\venv\Scripts\python.exe")
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
if not exist "%PY%" (
    echo First time on this PC: run Setup.bat in this folder instead.
    pause
    exit /b
)
"%PY%" "%~dp0configure.py" || goto :failed
"%PY%" "%~dp0make_subtitles.py" --prepare || goto :failed
echo.
echo Saved. The next video you drop in uses the new settings.
pause
exit /b 0

:failed
echo.
echo Couldn't finish. Check your internet connection and run Settings.bat again.
pause
exit /b 1
