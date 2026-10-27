@echo off
rem Like Make Subtitles, but also works out who is talking.
rem Put a 10-20 second clip of each person talking alone in the Voices folder,
rem named after them (e.g. Alex.wav), and names are filled in automatically.
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
set N=
set /p N=How many people talk? (Enter = use the Voices folder, or guess):
if "%N%"=="" set N=0
for %%F in (%*) do "%PY%" "%~dp0make_subtitles.py" "%%~F" --speakers %N%
pause
