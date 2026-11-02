@echo off
rem Run this once on each new PC. It installs Python (if needed), the packages and the
rem models into a folder on this PC, outside the Tools folder, so Google Drive won't sync them.
setlocal
rem install.ps1 runs this with --no-window: it opens the window itself, and nobody is there
rem to press a key, so a "pause" would wait for ever
set "NO_PAUSE="
if /i "%~1"=="--no-window" set "NO_PAUSE=1"
if defined SUBTITLE_MAKER_HOME (set "HOME_DIR=%SUBTITLE_MAKER_HOME%") else (set "HOME_DIR=%USERPROFILE%\.subtitle-maker")
set "VENV_PY=%HOME_DIR%\venv\Scripts\python.exe"
echo Setting up the subtitle maker in "%HOME_DIR%"
echo.

echo [1/3] Python
rem Find Python 3.10 - 3.12
set PY=
for %%V in (3.12 3.11 3.10) do if not defined PY py -%%V -c "pass" >nul 2>&1 && set PY=py -%%V
if not defined PY python -c "import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 12))" >nul 2>&1 && set PY=python
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
    echo Python 3.12 not found, installing it...
    winget install --id Python.Python.3.12 -e --scope user --silent --accept-source-agreements --accept-package-agreements
    if not exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" goto :no_python
    set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
echo Using Python: %PY%
echo.

rem Make the private Python environment, or remake it if it's broken (say, half-deleted by an
rem earlier uninstall): python.exe can be there while pip is missing
set "VENV_OK="
if exist "%VENV_PY%" "%VENV_PY%" -c "import pip" >nul 2>&1 && set "VENV_OK=1"
if not defined VENV_OK (
    if exist "%VENV_PY%" (echo The Python environment is incomplete, so making it again...) else (echo Creating private Python environment...)
    %PY% -m venv --clear "%HOME_DIR%\venv" || goto :failed
)
rem pip's own progress bars show the downloads, so a slow connection never looks frozen. The installer
rem window reads "raw" ones instead ("Progress 123 of 456"), if this pip has them (24.1 and later)
set "BAR=on"
if defined SUBTITLE_MAKER_PIP_RAW "%VENV_PY%" -c "import pip, sys; sys.exit(tuple(int(x) for x in pip.__version__.split('.')[:2]) < (24, 1))" >nul 2>&1 && set "BAR=raw"
set "PIP=-m pip install --disable-pip-version-check --no-input --progress-bar %BAR%"
echo [2/3] Python packages (about 300 MB to download the first time, then a few minutes to install)
call :pip -r "%~dp0requirements.txt" || goto :failed
echo.

echo [3/3] Graphics card support
where nvidia-smi >nul 2>&1
if not errorlevel 1 (
    echo NVIDIA GPU found: downloading GPU support ^(about 1 GB, the biggest step^)...
    call :pip -r "%~dp0requirements-gpu.txt" || goto :failed
) else (
    powershell -NoProfile -Command "if (Get-CimInstance Win32_VideoController | Where-Object Name -match 'NVIDIA') { exit 0 } else { exit 1 }" >nul 2>&1
    if not errorlevel 1 (
        echo.
        echo *** This PC has an NVIDIA graphics card, but its driver isn't installed. ***
        echo It will work on the CPU for now ^(about 3x slower^).
        echo For GPU speed: install the driver from https://www.nvidia.com/drivers
        echo or the NVIDIA App, restart, then run Setup.bat again.
        echo.
    ) else (
        echo No NVIDIA GPU found, so nothing to download: it will run on the CPU.
    )
)

rem The installer opens the setup window itself once it has finished
if defined NO_PAUSE exit /b 0

rem The rest (quality, language, where subtitles go, downloading models) happens in the setup window
echo.
echo Opening the setup window...
"%VENV_PY%" "%~dp0app.py" --setup
if not errorlevel 1 exit /b 0

rem No window on this PC (rare), so ask here instead
echo.
"%VENV_PY%" "%~dp0configure.py" || goto :failed
"%VENV_PY%" "%~dp0make_subtitles.py" --prepare || goto :failed
echo.
echo All set. Open "Subtitle Maker.bat", or drag a video onto "Make Subtitles (drop video here).bat".
pause
exit /b 0

:pip
rem Antivirus scanning the new files as pip unpacks them can make it fail with "Access is denied" or
rem "being used by another process", mostly on the first install; a dropped connection does the same.
rem So it tries again (already-downloaded packages are reused, so that's quick) before giving up.
"%VENV_PY%" %PIP% %* && exit /b 0
echo.
echo That didn't finish (antivirus checking the new files, or the connection dropped). Trying again...
"%VENV_PY%" %PIP% %* && exit /b 0
echo.
echo Trying one last time...
"%VENV_PY%" %PIP% %*
exit /b

:no_python
echo Couldn't install Python automatically. Install Python 3.12 from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH"), then run Setup.bat again.
if not defined NO_PAUSE pause
exit /b 1

:failed
echo.
echo Setup failed. Check your internet connection and run Setup.bat again.
if not defined NO_PAUSE pause
exit /b 1
