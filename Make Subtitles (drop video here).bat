@echo off
rem Drag a video or audio file onto this to make an .srt next to it.
if defined SUBTITLE_MAKER_HOME (set "PY=%SUBTITLE_MAKER_HOME%\venv\Scripts\python.exe") else (set "PY=%USERPROFILE%\.subtitle-maker\venv\Scripts\python.exe")
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
if not exist "%PY%" (
    echo First time on this PC: run Setup.bat in this folder, then try again.
    pause
    exit /b
)
if "%~1"=="" (
    echo Drag a video or audio file onto this icon.
    pause
    exit /b
)
for %%F in (%*) do "%PY%" "%~dp0make_subtitles.py" "%%~F"
pause
