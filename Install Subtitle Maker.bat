<# : batch portion
@echo off
rem The top half of this file is batch, which PowerShell skips as a comment; the bottom half is PowerShell.
rem Installs Subtitle Maker on this PC from GitHub, the latest version. Just double-click it.
rem Running it again later repairs or updates the install. Needs internet, nothing else.
setlocal
set "REPO=carrotsz19/subtitle-maker"
if not defined SUBTITLE_MAKER_INSTALL set "SUBTITLE_MAKER_INSTALL=%USERPROFILE%\Subtitle Maker"
set "DEST=%SUBTITLE_MAKER_INSTALL%"
if defined SUBTITLE_MAKER_HOME (set "DATA=%SUBTITLE_MAKER_HOME%") else (set "DATA=%USERPROFILE%\.subtitle-maker")
set "SELF=%~f0"
title Installing Subtitle Maker
echo Installing Subtitle Maker into "%DEST%"
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression (Get-Content -LiteralPath $env:SELF -Raw)"
if errorlevel 1 goto :failed
set /p SHA=<"%DATA%\install-sha.txt"

rem Python, packages, quality level and models: the same Setup the Tools folder always had
call "%DEST%\Setup.bat"
if errorlevel 1 goto :failed
"%DATA%\venv\Scripts\python.exe" "%DEST%\update.py" --record %SHA%

powershell -NoProfile -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Subtitle Maker.lnk'); $s.TargetPath = $env:DEST + '\Subtitle Maker.bat'; $s.WorkingDirectory = $env:DEST; $s.IconLocation = $env:SystemRoot + '\System32\imageres.dll,18'; $s.WindowStyle = 7; $s.Save()"
echo.
echo Installed. There's a "Subtitle Maker" shortcut on your desktop. Opening it now...
start "" "%DEST%\Subtitle Maker.bat"
timeout /t 5 >nul
exit /b 0

:failed
echo.
echo The install didn't finish. Check your internet connection and run this file again.
pause
exit /b 1
#>

# ---- PowerShell part: download the latest version from GitHub into $env:DEST ----------------
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is many times slower with its progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = $env:REPO
$api = if ($env:SUBTITLE_MAKER_API) { $env:SUBTITLE_MAKER_API } else { 'https://api.github.com' }  # override for testing
New-Item -ItemType Directory -Force -Path $env:DATA | Out-Null
$headers = @{ 'User-Agent' = 'subtitle-maker-installer'; 'Accept' = 'application/vnd.github+json' }

try {
    $commit = Invoke-RestMethod -Headers $headers -Uri "$api/repos/$repo/commits/main" -UseBasicParsing
} catch {
    Write-Host "Couldn't get Subtitle Maker from GitHub: $($_.Exception.Message)"
    exit 1
}

$sha = $commit.sha
Write-Host "Downloading the latest version ($($sha.Substring(0, 7)))..."
$zip = Join-Path $env:TEMP "subtitle-maker-$sha.zip"
$unpacked = Join-Path $env:TEMP "subtitle-maker-$sha"
Invoke-WebRequest -Headers $headers -Uri "$api/repos/$repo/zipball/$sha" -OutFile $zip -UseBasicParsing
if (Test-Path $unpacked) { Remove-Item -LiteralPath $unpacked -Recurse -Force }
Expand-Archive -LiteralPath $zip -DestinationPath $unpacked -Force
$root = (Get-ChildItem -LiteralPath $unpacked -Directory | Select-Object -First 1).FullName
if (-not (Test-Path (Join-Path $root 'make_subtitles.py'))) { Write-Host "The download didn't look right."; exit 1 }

# Merge into the install folder: program files are replaced, anything of yours (Voices clips) stays
New-Item -ItemType Directory -Force -Path $env:DEST | Out-Null
robocopy $root $env:DEST /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Host "Couldn't copy the files into $env:DEST."; exit 1 }
# The exact files this version has: updates replace and Uninstall removes these, nothing else
$files = Get-ChildItem -LiteralPath $root -Recurse -File | ForEach-Object { $_.FullName.Substring($root.Length + 1).Replace('\', '/') }
[IO.File]::WriteAllLines((Join-Path $env:DATA 'install-files.txt'), [string[]]$files)
Remove-Item -LiteralPath $zip, $unpacked -Recurse -Force
Set-Content -LiteralPath (Join-Path $env:DATA 'install-sha.txt') -Value $sha -NoNewline -Encoding Ascii
Write-Host "Downloaded."
Write-Host ""
exit 0
