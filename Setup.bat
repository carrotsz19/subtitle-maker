@echo off
rem Run this once on each new PC. It installs Python (if needed), the packages and the
rem models into a folder on this PC, outside the Tools folder, so Google Drive won't sync them.
setlocal
if defined SUBTITLE_MAKER_HOME (set "HOME_DIR=%SUBTITLE_MAKER_HOME%") else (set "HOME_DIR=%USERPROFILE%\.subtitle-maker")
set "VENV_PY=%HOME_DIR%\venv\Scripts\python.exe"
echo Setting up the subtitle maker in "%HOME_DIR%"
echo.

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

if not exist "%VENV_PY%" (
    echo Creating private Python environment...
    %PY% -m venv "%HOME_DIR%\venv" || goto :failed
)
echo Installing packages (a few minutes the first time)...
"%VENV_PY%" -m pip install --quiet --upgrade pip || goto :failed
"%VENV_PY%" -m pip install --quiet -r "%~dp0requirements.txt" || goto :failed

where nvidia-smi >nul 2>&1
if not errorlevel 1 (
    echo NVIDIA GPU found, installing GPU support (about 1 GB^)...
    "%VENV_PY%" -m pip install --quiet -r "%~dp0requirements-gpu.txt" || goto :failed
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
        echo No NVIDIA GPU found, it will run on the CPU.
    )
)

echo.
"%VENV_PY%" "%~dp0configure.py" || goto :failed
"%VENV_PY%" "%~dp0make_subtitles.py" --prepare || goto :failed
echo.
echo All set. Open "Subtitle Maker.bat", or drag a video onto "Make Subtitles (drop video here).bat".
pause
exit /b 0

:no_python
echo Couldn't install Python automatically. Install Python 3.12 from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH"), then run Setup.bat again.
pause
exit /b 1

:failed
echo.
echo Setup failed. Check your internet connection and run Setup.bat again.
pause
exit /b 1
