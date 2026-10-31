# Installs (or repairs, or updates) Subtitle Maker on this PC, with no download warnings:
# open PowerShell or Terminal and paste
#
#     irm https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1 | iex
#
# It downloads the latest version into %USERPROFILE%\Subtitle Maker, installs Python and its
# packages (Setup.bat), puts a shortcut on the desktop and opens the setup window.
# "Install Subtitle Maker.bat" runs this same script for people who'd rather double-click.
#
# For the experimental version (newer, less tested; Settings > Updates switches back to Stable):
#
#     $env:SUBTITLE_MAKER_BRANCH = 'experimental'; irm https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1 | iex

function Install-SubtitleMaker {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is many times slower with its progress bar
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    $repo = 'carrotsz19/subtitle-maker'
    $api = if ($env:SUBTITLE_MAKER_API) { $env:SUBTITLE_MAKER_API } else { 'https://api.github.com' }  # override for testing
    # GitHub's API allows 60 requests an hour per internet address, shared by everybody on a school or
    # office network, so the latest version is found from the commits feed and downloaded from
    # codeload (ordinary GitHub pages, no such limit). The API is only the fallback.
    $web = if ($env:SUBTITLE_MAKER_WEB) { $env:SUBTITLE_MAKER_WEB } else { 'https://github.com' }
    $codeload = if ($env:SUBTITLE_MAKER_CODELOAD) { $env:SUBTITLE_MAKER_CODELOAD } else { 'https://codeload.github.com' }
    $dest = if ($env:SUBTITLE_MAKER_INSTALL) { $env:SUBTITLE_MAKER_INSTALL } else { Join-Path $env:USERPROFILE 'Subtitle Maker' }
    $data = if ($env:SUBTITLE_MAKER_HOME) { $env:SUBTITLE_MAKER_HOME } else { Join-Path $env:USERPROFILE '.subtitle-maker' }
    $branch = if ($env:SUBTITLE_MAKER_BRANCH) { $env:SUBTITLE_MAKER_BRANCH } else { 'main' }
    $headers = @{ 'User-Agent' = 'subtitle-maker-installer'; 'Accept' = 'application/vnd.github+json' }
    $plain = @{ 'User-Agent' = 'subtitle-maker-installer' }
    # GitHub limits anonymous requests per network; the Windows tests share theirs, so they pass a token
    if ($env:SUBTITLE_MAKER_GITHUB_TOKEN) { $headers['Authorization'] = "Bearer $env:SUBTITLE_MAKER_GITHUB_TOKEN" }

    Write-Host ''
    Write-Host "Installing Subtitle Maker into $dest" -ForegroundColor Cyan
    Write-Host ''
    New-Item -ItemType Directory -Force -Path $data | Out-Null

    # ---- 1. The latest version from GitHub ----------------------------------------------------
    $sha = $null
    try {
        [xml]$feed = (Invoke-WebRequest -Headers $plain -Uri "$web/$repo/commits/$branch.atom" -UseBasicParsing).Content
        $sha = (@($feed.feed.entry)[0].id -split '/')[-1]
        if ($sha -notmatch '^[0-9a-f]{40}$') { $sha = $null }
    } catch { }
    if (-not $sha) {
        try {
            $sha = (Invoke-RestMethod -Headers $headers -Uri "$api/repos/$repo/commits/$branch" -UseBasicParsing).sha
        } catch {
            Write-Host "Couldn't reach GitHub: $($_.Exception.Message)" -ForegroundColor Red
            return $false
        }
    }
    $which = if ($branch -eq 'main') { '' } else { "$branch " }
    Write-Host "Downloading the latest ${which}version ($($sha.Substring(0, 7)))..."
    $zip = Join-Path $env:TEMP "subtitle-maker-$sha.zip"
    $unpacked = Join-Path $env:TEMP "subtitle-maker-$sha"
    try {
        Invoke-WebRequest -Headers $plain -Uri "$codeload/$repo/zip/$sha" -OutFile $zip -UseBasicParsing
    } catch {
        Invoke-WebRequest -Headers $headers -Uri "$api/repos/$repo/zipball/$sha" -OutFile $zip -UseBasicParsing
    }
    if (Test-Path $unpacked) { Remove-Item -LiteralPath $unpacked -Recurse -Force }
    Expand-Archive -LiteralPath $zip -DestinationPath $unpacked -Force
    $root = (Get-ChildItem -LiteralPath $unpacked -Directory | Select-Object -First 1).FullName
    if (-not (Test-Path (Join-Path $root 'make_subtitles.py'))) {
        Write-Host "The download didn't look right. Nothing was changed." -ForegroundColor Red
        return $false
    }

    # The exact files this version has: updates replace and Uninstall removes these, nothing else
    # (tests and .github are for developing it, not part of an install)
    foreach ($dev in 'tests', '.github') {
        $devPath = Join-Path $root $dev
        if (Test-Path $devPath) { Remove-Item -LiteralPath $devPath -Recurse -Force }
    }
    $files = Get-ChildItem -LiteralPath $root -Recurse -File | ForEach-Object { $_.FullName.Substring($root.Length + 1).Replace('\', '/') }
    [IO.File]::WriteAllLines((Join-Path $data 'install-files.txt'), [string[]]$files)

    # Merge into the install folder: program files are replaced, anything of yours (Voices clips) stays
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    robocopy $root $dest /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Write-Host "Couldn't copy the files into $dest." -ForegroundColor Red
        return $false
    }
    Remove-Item -LiteralPath $zip, $unpacked -Recurse -Force
    Write-Host ''

    # ---- 2. Python and its packages (the window takes it from there) ------------------------
    & cmd.exe /c "`"$dest\Setup.bat`" --no-window"
    if ($LASTEXITCODE -ne 0) { return $false }
    $python = Join-Path $data 'venv\Scripts\python.exe'
    & $python (Join-Path $dest 'update.py') --record $sha --channel $branch

    # ---- 3. Desktop shortcut, with the icon --------------------------------------------------
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut(
        (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Subtitle Maker.lnk'))
    $shortcut.TargetPath = Join-Path $data 'venv\Scripts\pythonw.exe'
    $shortcut.Arguments = "`"$(Join-Path $dest 'app.py')`""
    $shortcut.WorkingDirectory = $dest
    $shortcut.IconLocation = Join-Path $dest 'ui\icon.ico'
    $shortcut.Description = 'Make subtitles for DaVinci Resolve'
    $shortcut.Save()

    Write-Host ''
    Write-Host 'Installed. There''s a "Subtitle Maker" shortcut on your desktop.' -ForegroundColor Green
    if ($env:SUBTITLE_MAKER_NO_LAUNCH) { return $true }  # the Windows tests on GitHub
    Write-Host 'Opening the setup window to choose the quality and download what it needs...'
    Start-Process -FilePath (Join-Path $data 'venv\Scripts\pythonw.exe') -ArgumentList "`"$(Join-Path $dest 'app.py')`" --setup" -WorkingDirectory $dest
    return $true
}

# A function (not the script itself) does the work, so a problem never closes the PowerShell
# window it was pasted into; "Install Subtitle Maker.bat" reads the result from this variable
$global:SubtitleMakerInstalled = Install-SubtitleMaker
