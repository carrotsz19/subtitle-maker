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
    # A new name each time, so a folder left locked by an earlier try (antivirus still scanning it)
    # can't get in the way
    $run = [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $zip = Join-Path $env:TEMP "subtitle-maker-$sha-$run.zip"
    $unpacked = Join-Path $env:TEMP "subtitle-maker-$sha-$run"
    try {
        Invoke-WebRequest -Headers $plain -Uri "$codeload/$repo/zip/$sha" -OutFile $zip -UseBasicParsing
    } catch {
        Invoke-WebRequest -Headers $headers -Uri "$api/repos/$repo/zipball/$sha" -OutFile $zip -UseBasicParsing
    }
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
    Remove-Item -LiteralPath $zip, $unpacked -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host ''

    # ---- 2. Python and its packages (the window takes it from there) ------------------------
    # Started as its own process in this window, so what it prints (each step, pip's download bars)
    # shows as it happens. Run with & inside this function, all of it was collected as the
    # function's result instead: the window sat silent for many minutes, and the result
    # (an array of that text) counted as success even when it failed.
    $setup = Start-Process -FilePath $env:ComSpec -ArgumentList "/c `"`"$dest\Setup.bat`" --no-window`"" `
        -NoNewWindow -Wait -PassThru
    if ($setup.ExitCode -ne 0) {
        Write-Host ''
        Write-Host 'Installing Python and its packages failed (the reason is above). Paste the install line again to carry on from here.' -ForegroundColor Red
        return $false
    }
    $python = Join-Path $data 'venv\Scripts\python.exe'
    & $python (Join-Path $dest 'update.py') --record $sha --channel $branch | Out-Host

    # ---- 3. Desktop shortcut, with the icon --------------------------------------------------
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut(
        (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Subtitle Maker.lnk'))
    $shortcut.TargetPath = Join-Path $data 'venv\Scripts\pythonw.exe'
    $shortcut.Arguments = "`"$(Join-Path $dest 'app.py')`""
    $shortcut.WorkingDirectory = $dest
    $shortcut.IconLocation = Join-Path $dest 'ui\icon.ico'
    $shortcut.Description = 'Make subtitles for DaVinci Resolve'
    $shortcut.Save()

    # ---- 4. A proper Windows app: Start menu entry and Settings > Apps -------------------------
    & $python (Join-Path $dest 'winapp.py') | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Host "Couldn't add it to the Start menu; it does that itself the first time it opens." -ForegroundColor Yellow }

    Write-Host ''
    Write-Host 'Installed. There''s a "Subtitle Maker" shortcut on your desktop.' -ForegroundColor Green
    if ($env:SUBTITLE_MAKER_NO_LAUNCH) { return $true }  # the Windows tests on GitHub
    Write-Host 'Opening the Subtitle Maker window...'
    $log = Join-Path $data 'startup.log'
    $launched = Get-Date
    $app = Start-Process -FilePath (Join-Path $data 'venv\Scripts\pythonw.exe') -ArgumentList "`"$(Join-Path $dest 'app.py')`" --setup" `
        -WorkingDirectory $dest -PassThru
    # Wait until it says it's ready (startup.log is rewritten as it opens), so this window can say
    # it's done instead of stopping with no word on whether anything happened
    $deadline = $launched.AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if ((Test-Path $log) -and (Get-Item $log).LastWriteTime -ge $launched -and
            (Get-Content $log -Raw) -match 'window ready to use') {
            Write-Host ''
            Write-Host 'Done. Subtitle Maker is open: pick the quality there and it downloads the rest.' -ForegroundColor Green
            Write-Host 'You can close this window.'
            return $true
        }
        if ($app.HasExited) {
            Write-Host ''
            Write-Host 'The Subtitle Maker window closed straight away.' -ForegroundColor Red
            $errors = Join-Path $data 'last_error.log'
            if ((Test-Path $errors) -and (Get-Item $errors).LastWriteTime -ge $launched) {
                Write-Host (Get-Content $errors -Raw)
                Write-Host "(saved in $errors)"
            }
            Write-Host 'Everything is installed, so try the "Subtitle Maker" shortcut on your desktop.' -ForegroundColor Yellow
            return $true   # the install itself worked
        }
    }
    Write-Host ''
    Write-Host 'Installed. The window is taking a while to open; it will appear by itself (or use the desktop shortcut).' -ForegroundColor Yellow
    return $true
}

function Install-SubtitleMakerSafely {
    # Anything unexpected (a download cut off, antivirus holding a file) is said plainly here,
    # instead of escaping as a red PowerShell error with no "it failed" and no result
    try {
        return [bool](Install-SubtitleMaker | Select-Object -Last 1)
    } catch {
        Write-Host ''
        Write-Host "The install stopped: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host 'Nothing you made is lost. Check your internet connection and paste the install line again.' -ForegroundColor Red
        return $false
    }
}

# A function (not the script itself) does the work, so a problem never closes the PowerShell
# window it was pasted into; "Install Subtitle Maker.bat" reads the result from this variable
$global:SubtitleMakerInstalled = Install-SubtitleMakerSafely
