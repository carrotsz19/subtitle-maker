# Installs (or repairs, or updates) Subtitle Maker on this PC, with no download warnings:
# open PowerShell or Terminal and paste
#
#     irm https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1 | iex
#
# It downloads the latest version into %USERPROFILE%\Subtitle Maker, installs Python and its
# packages (Setup.bat), puts a shortcut on the desktop and opens the setup window.
#
# "Install Subtitle Maker.bat" runs this same script for people who'd rather double-click, with
# SUBTITLE_MAKER_GUI=1: then there's no console at all, just the Veyra installer window (made
# here with WPF, which every Windows PC has, since there's no Python yet to show the app's own).
#
# For the experimental version (newer, less tested; Settings > Updates switches back to Stable):
#
#     $env:SUBTITLE_MAKER_BRANCH = 'experimental'; irm https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1 | iex

# ---- Saying what's happening: to the installer window if there is one, or to this console ----
# The window runs on its own thread and reads this shared table; the install itself runs here.
$script:SubtitleMakerUi = $null

function Show-InstallLine([string] $Text, [string] $Color) {
    $ui = $script:SubtitleMakerUi
    if ($ui) {
        $ui.Log.Enqueue($Text)
        if ($Color -eq 'Red' -and $Text.Trim()) { $ui.Error = $Text.Trim() }
    } elseif ($Color) { Write-Host $Text -ForegroundColor $Color } else { Write-Host $Text }
}

function Set-InstallStage([int] $Stage, [string] $Status) {
    $ui = $script:SubtitleMakerUi
    if (-not $ui) { return }
    $ui.Stage = $Stage; $ui.Status = $Status; $ui.Sub = ''; $ui.Progress = -1
}

function Set-InstallStatus([string] $Status, $Sub = $null, $Progress = $null) {
    $ui = $script:SubtitleMakerUi
    if (-not $ui) { return }
    if ($Status) { $ui.Status = $Status }
    if ($null -ne $Sub) { $ui.Sub = $Sub }
    if ($null -ne $Progress) { $ui.Progress = $Progress }
}

function Test-InstallCancelled { [bool]($script:SubtitleMakerUi -and $script:SubtitleMakerUi.Cancel) }

function Format-MB([double] $Bytes) { if ($Bytes -ge 1GB) { '{0:0.0} GB' -f ($Bytes / 1GB) } else { '{0:0} MB' -f ($Bytes / 1MB) } }

# What Setup.bat prints, turned into the window's steps and progress bar
function Read-SetupLine([string] $Line) {
    $ui = $script:SubtitleMakerUi
    $dl = $script:SubtitleMakerDownload
    if ($Line -match '^Progress (\d+) of (\d+)') {
        # pip --progress-bar raw: bytes of the file it's downloading. A new file starts from 0.
        $now = [double]$Matches[1]; $total = [double]$Matches[2]
        if ($now -lt $dl.LastNow -or $total -ne $dl.LastTotal) { $dl.Done += $dl.LastNow }
        $dl.LastNow = $now; $dl.LastTotal = $total
        $got = $dl.Done + $now
        if ($dl.Expected -gt 0) {
            Set-InstallStatus ("Downloading: {0} of about {1}" -f (Format-MB $got), (Format-MB $dl.Expected)) $null ([Math]::Min(0.97, $got / $dl.Expected))
        }
        return
    }
    # winget's own progress while Python downloads: a bar, then "10.0 MB / 25.4 MB"
    if ($Line -match '([\d.]+)\s*(KB|MB|GB)\s*/\s*([\d.]+)\s*(KB|MB|GB)') {
        $unit = @{ KB = 1KB; MB = 1MB; GB = 1GB }
        $a = [double]$Matches[1] * $unit[$Matches[2]]; $b = [double]$Matches[3] * $unit[$Matches[4]]
        if ($b -gt 0) { Set-InstallStatus ("Downloading Python: {0} of {1}" -f (Format-MB $a), (Format-MB $b)) $null ([Math]::Min(1, $a / $b)) }
        return
    }
    if ($Line.Trim() -and $Line -notmatch '[A-Za-z0-9]') { return }  # winget's spinner and bars
    Show-InstallLine $Line
    if ($Line -match '^\[1/3\]') { Set-InstallStage 1 'Getting Python ready' }
    elseif ($Line -match 'Python 3\.12 not found') { Set-InstallStatus 'Installing Python 3.12 (about 25 MB)' }
    elseif ($Line -match 'Creating private Python environment|making it again') { Set-InstallStatus 'Making a private Python environment' }
    elseif ($Line -match '^\[2/3\]') {
        Set-InstallStage 2 'Downloading the app''s packages'
        $script:SubtitleMakerDownload = @{ Expected = 300MB; Done = 0; LastNow = 0; LastTotal = 0 }
    }
    elseif ($Line -match '^\[3/3\]') {
        Set-InstallStage 3 'Checking for an NVIDIA graphics card'
        $script:SubtitleMakerDownload = @{ Expected = 1.1GB; Done = 0; LastNow = 0; LastTotal = 0 }
    }
    elseif ($Line -match 'No NVIDIA GPU found') { $ui.Skip3 = $true; Set-InstallStatus 'No NVIDIA graphics card, so nothing to download' 'It runs on the processor instead.' 1 }
    elseif ($Line -match '^NVIDIA GPU found') { Set-InstallStatus 'Downloading graphics card support (the biggest step)' '' 0 }
    elseif ($Line -match "driver isn't installed") { $ui.Skip3 = $true; Set-InstallStatus 'NVIDIA card found, but its driver isn''t installed' 'It works on the processor for now. Install the NVIDIA driver later for 3x the speed.' 1 }
    elseif ($Line -match '^\s*(?:Downloading|Using cached) ([A-Za-z0-9_.]+?)-\d.*?(?:\((.+)\))?$') {
        $size = if ($Matches[2]) { " ($($Matches[2]))" } else { '' }
        Set-InstallStatus $null "$($Matches[1])$size"
    }
    elseif ($Line -match '^Installing collected packages') { Set-InstallStatus 'Installing the packages (a few minutes, nothing more to download)' '' -1 }
    elseif ($Line -match "^That didn't finish") { Set-InstallStatus 'That was interrupted (antivirus or the connection), so trying again' '' -1 }
    elseif ($Line -match '^(?:ERROR|Setup failed|Couldn''t install Python)') { $ui.Error = $Line.Trim() }
}

# Setup.bat: Python and its packages. In this console, it prints straight into it as it goes;
# for the window, its output is read line by line instead (it never shows a console).
function Invoke-SubtitleMakerSetup([string] $Dest) {
    if (-not $script:SubtitleMakerUi) {
        $setup = Start-Process -FilePath $env:ComSpec -ArgumentList "/c `"`"$Dest\Setup.bat`" --no-window`"" `
            -NoNewWindow -Wait -PassThru
        return $setup.ExitCode
    }
    $info = New-Object System.Diagnostics.ProcessStartInfo $env:ComSpec, "/c `"`"$Dest\Setup.bat`" --no-window 2>&1`""
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.CreateNoWindow = $true
    $info.EnvironmentVariables['SUBTITLE_MAKER_PIP_RAW'] = '1'   # pip says how far each download is
    $info.EnvironmentVariables['PYTHONUNBUFFERED'] = '1'
    $process = [System.Diagnostics.Process]::Start($info)
    $script:SubtitleMakerUi.Pid = $process.Id                    # so Cancel can stop it
    $script:SubtitleMakerDownload = @{ Expected = 0; Done = 0; LastNow = 0; LastTotal = 0 }
    while ($null -ne ($line = $process.StandardOutput.ReadLine())) { Read-SetupLine $line }
    $process.WaitForExit()
    $script:SubtitleMakerUi.Pid = 0
    return $process.ExitCode
}

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

    Show-InstallLine ''
    Show-InstallLine "Installing Subtitle Maker into $dest" Cyan
    Show-InstallLine ''
    Set-InstallStage 0 'Finding the latest version'
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
            Show-InstallLine "Couldn't reach GitHub: $($_.Exception.Message)" Red
            return $false
        }
    }
    $which = if ($branch -eq 'main') { '' } else { "$branch " }
    Show-InstallLine "Downloading the latest ${which}version ($($sha.Substring(0, 7)))..."
    Set-InstallStatus "Downloading the latest ${which}version" $sha.Substring(0, 7)
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
        Show-InstallLine "The download didn't look right. Nothing was changed." Red
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
        Show-InstallLine "Couldn't copy the files into $dest." Red
        return $false
    }
    Remove-Item -LiteralPath $zip, $unpacked -Recurse -Force -ErrorAction SilentlyContinue
    Show-InstallLine ''
    if (Test-InstallCancelled) { return $false }

    # ---- 2. Python and its packages (the window takes it from there) ------------------------
    # Not run with & inside this function: everything it printed was collected as the function's
    # result instead of shown, so the console sat silent for many minutes, and that result (an
    # array of text) counted as success even when it failed.
    Set-InstallStage 1 'Getting Python ready'
    if ((Invoke-SubtitleMakerSetup $dest) -ne 0) {
        if (Test-InstallCancelled) { return $false }
        Show-InstallLine ''
        $why = if ($script:SubtitleMakerUi -and $script:SubtitleMakerUi.Error) { $script:SubtitleMakerUi.Error } else { 'the reason is above' }
        Show-InstallLine "Installing Python and its packages failed ($why). Run the installer again to carry on from here." Red
        return $false
    }
    if (Test-InstallCancelled) { return $false }

    Set-InstallStage 4 'Finishing up'
    $python = Join-Path $data 'venv\Scripts\python.exe'
    & $python (Join-Path $dest 'update.py') --record $sha --channel $branch | ForEach-Object { Show-InstallLine "$_" }

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
    Set-InstallStatus 'Adding it to the Start menu'
    & $python (Join-Path $dest 'winapp.py') | ForEach-Object { Show-InstallLine "$_" }
    if ($LASTEXITCODE -ne 0) { Show-InstallLine "Couldn't add it to the Start menu; it does that itself the first time it opens." Yellow }

    Show-InstallLine ''
    Show-InstallLine 'Installed. There''s a "Subtitle Maker" shortcut on your desktop.' Green
    if ($env:SUBTITLE_MAKER_NO_LAUNCH) { return $true }  # the Windows tests on GitHub
    Show-InstallLine 'Opening the Subtitle Maker window...'
    Set-InstallStatus 'Opening Subtitle Maker' '' -1
    $log = Join-Path $data 'startup.log'
    $launched = Get-Date
    $app = Start-Process -FilePath (Join-Path $data 'venv\Scripts\pythonw.exe') -ArgumentList "`"$(Join-Path $dest 'app.py')`" --setup" `
        -WorkingDirectory $dest -PassThru
    # Wait until it says it's ready (startup.log is rewritten as it opens), so this can say it's
    # done instead of stopping with no word on whether anything happened
    $deadline = $launched.AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if ((Test-Path $log) -and (Get-Item $log).LastWriteTime -ge $launched -and
            (Get-Content $log -Raw) -match 'window ready to use') {
            Show-InstallLine ''
            Show-InstallLine 'Done. Subtitle Maker is open: pick the quality there and it downloads the rest.' Green
            Show-InstallLine 'You can close this window.'
            if ($script:SubtitleMakerUi) { $script:SubtitleMakerUi.State = 'done' }
            return $true
        }
        if ($app.HasExited) {
            Show-InstallLine ''
            $errors = Join-Path $data 'last_error.log'
            if ((Test-Path $errors) -and (Get-Item $errors).LastWriteTime -ge $launched) {
                Show-InstallLine (Get-Content $errors -Raw)
                Show-InstallLine "(saved in $errors)"
            }
            Show-InstallLine 'Everything is installed, but the Subtitle Maker window closed straight away. Try the "Subtitle Maker" shortcut on your desktop.' Red
            return [bool](-not $script:SubtitleMakerUi)   # the install itself worked; the window offers Try again
        }
    }
    Show-InstallLine ''
    Show-InstallLine 'Installed. The window is taking a while to open; it will appear by itself (or use the desktop shortcut).' Yellow
    if ($script:SubtitleMakerUi) { $script:SubtitleMakerUi.State = 'slow' }
    return $true
}

function Install-SubtitleMakerSafely {
    # Anything unexpected (a download cut off, antivirus holding a file) is said plainly here,
    # instead of escaping as a red PowerShell error with no "it failed" and no result
    try {
        return [bool](Install-SubtitleMaker | Select-Object -Last 1)
    } catch {
        Show-InstallLine ''
        Show-InstallLine "The install stopped: $($_.Exception.Message)" Red
        Show-InstallLine 'Nothing you made is lost. Check your internet connection and run the installer again.' Red
        return $false
    }
}

# ---- The installer window (Veyra's look, the same colours and logo as the app) ----------------
# Runs in a runspace of its own (WPF needs an STA thread, and the install must not freeze it). It
# only reads and writes the shared table: Stage 0-4, Status, Sub, Progress (0-1, or -1 for "busy"),
# State (working, done, slow, failed, cancelled), Error, and Cancel / Retry / Closed from the buttons.
$script:SubtitleMakerWindow = @'
param($sync)
try {
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
    $dark = $false
    try { $dark = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -ErrorAction Stop).AppsUseLightTheme -eq 0 } catch { }
    $c = if ($dark) {
        @{ Bg = '#15140F'; Card = '#1E1C17'; Fill = '#272420'; Line = '#37342C'; Ink = '#F2EFE8'; Dim = '#A29C8F'; Faint = '#5E5A52'
           Tint = '#D9633E'; OnTint = '#161310'; Ok = '#7FAE87'; Shadow = '#000000'; Hover = '#2E2B26' }
    } else {
        @{ Bg = '#F3F1EC'; Card = '#FFFFFF'; Fill = '#ECEAE5'; Line = '#DCD8CF'; Ink = '#1B1A18'; Dim = '#6D6961'; Faint = '#B9B5AC'
           Tint = '#B3492B'; OnTint = '#FFFFFF'; Ok = '#3F6B4A'; Shadow = '#1B1A18'; Hover = '#E4E1DA' }
    }
    $xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Install Subtitle Maker" Width="540" SizeToContent="Height"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent" ResizeMode="NoResize"
        WindowStartupLocation="CenterScreen" FontFamily="Segoe UI Variable Text, Segoe UI" FontSize="13"
        Foreground="$($c.Ink)" Opacity="0" UseLayoutRounding="True" TextOptions.TextFormattingMode="Ideal">
  <Window.Resources>
    <Style x:Key="Btn" TargetType="Button">
      <Setter Property="Foreground" Value="$($c.Ink)"/>
      <Setter Property="Background" Value="$($c.Fill)"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Padding" Value="18,8"/>
      <Setter Property="Margin" Value="8,0,0,0"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Focusable" Value="False"/>
      <Setter Property="Template"><Setter.Value>
        <ControlTemplate TargetType="Button">
          <Border x:Name="b" Background="{TemplateBinding Background}" CornerRadius="10" Padding="{TemplateBinding Padding}">
            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
          </Border>
          <ControlTemplate.Triggers>
            <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="b" Property="Opacity" Value="0.86"/></Trigger>
            <Trigger Property="IsPressed" Value="True"><Setter TargetName="b" Property="Opacity" Value="0.72"/></Trigger>
          </ControlTemplate.Triggers>
        </ControlTemplate>
      </Setter.Value></Setter>
    </Style>
    <Style x:Key="Primary" TargetType="Button" BasedOn="{StaticResource Btn}">
      <Setter Property="Background" Value="$($c.Tint)"/>
      <Setter Property="Foreground" Value="$($c.OnTint)"/>
    </Style>
    <Style x:Key="Ghost" TargetType="Button">
      <Setter Property="Foreground" Value="$($c.Dim)"/>
      <Setter Property="Focusable" Value="False"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="FontFamily" Value="Segoe Fluent Icons, Segoe MDL2 Assets"/>
      <Setter Property="FontSize" Value="10"/>
      <Setter Property="Template"><Setter.Value>
        <ControlTemplate TargetType="Button">
          <Border x:Name="b" Background="Transparent" CornerRadius="8" Width="30" Height="30">
            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
          </Border>
          <ControlTemplate.Triggers>
            <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="b" Property="Background" Value="$($c.Hover)"/></Trigger>
          </ControlTemplate.Triggers>
        </ControlTemplate>
      </Setter.Value></Setter>
    </Style>
  </Window.Resources>
  <Border x:Name="Shell" Margin="20" CornerRadius="18" Background="$($c.Bg)" BorderBrush="$($c.Line)" BorderThickness="1">
    <Border.Effect><DropShadowEffect BlurRadius="30" ShadowDepth="8" Direction="270" Opacity="0.22" Color="$($c.Shadow)"/></Border.Effect>
    <Border.RenderTransform><TranslateTransform x:Name="Lift" Y="10"/></Border.RenderTransform>
    <Grid Margin="26,20,20,24">
      <Grid.RowDefinitions>
        <RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/>
        <RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>

      <Grid Grid.Row="0">
        <Grid.ColumnDefinitions><ColumnDefinition Width="Auto"/><ColumnDefinition/><ColumnDefinition Width="Auto"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
        <Viewbox x:Name="Logo" Width="48" Height="48" Margin="0,6,0,0">
          <Canvas Width="1024" Height="1024">
            <Rectangle Width="1024" Height="1024" RadiusX="225" RadiusY="225" Fill="#F3F1EC" Stroke="#171B1A18" StrokeThickness="22"/>
            <Line X1="762" Y1="267" X2="512" Y2="766" Stroke="#3F6B4A" StrokeThickness="128" StrokeStartLineCap="Round" StrokeEndLineCap="Round"/>
            <Line X1="262" Y1="267" X2="512" Y2="766" Stroke="#B3492B" StrokeThickness="128" StrokeStartLineCap="Round" StrokeEndLineCap="Round"/>
            <Line X1="262" Y1="267" X2="512" Y2="766" Stroke="#1C1B19" Opacity="0.2" StrokeThickness="128" StrokeStartLineCap="Round" StrokeEndLineCap="Round">
              <Line.Clip>
                <GeometryGroup FillRule="Nonzero">
                  <PathGeometry Figures="M819.2,295.7 L704.8,238.3 L454.8,737.3 L569.2,794.7 Z"/>
                  <EllipseGeometry Center="512,766" RadiusX="64" RadiusY="64"/>
                </GeometryGroup>
              </Line.Clip>
            </Line>
          </Canvas>
        </Viewbox>
        <StackPanel Grid.Column="1" Margin="14,6,0,0" VerticalAlignment="Center">
          <TextBlock Text="VEYRA" FontSize="10.5" FontWeight="Bold" Foreground="$($c.Tint)"/>
          <TextBlock Text="Subtitle Maker" FontSize="21" FontWeight="Bold" FontFamily="Segoe UI Variable Display, Segoe UI"/>
        </StackPanel>
        <Button x:Name="MinButton" Grid.Column="2" Style="{StaticResource Ghost}" Content="&#xE921;" VerticalAlignment="Top" ToolTip="Minimise"/>
        <Button x:Name="CloseButton" Grid.Column="3" Style="{StaticResource Ghost}" Content="&#xE8BB;" VerticalAlignment="Top" ToolTip="Close"/>
      </Grid>

      <TextBlock x:Name="Headline" Grid.Row="1" Margin="0,22,6,0" FontSize="17" FontWeight="SemiBold"
                 FontFamily="Segoe UI Variable Display, Segoe UI" Text="Installing Subtitle Maker"/>
      <TextBlock x:Name="Intro" Grid.Row="2" Margin="0,3,6,0" Foreground="$($c.Dim)" TextWrapping="Wrap"
                 Text="About 5 to 15 minutes, mostly downloading. You can keep using your PC while it works."/>

      <Border Grid.Row="3" Margin="0,16,6,0" CornerRadius="14" Background="$($c.Card)" BorderBrush="$($c.Line)" BorderThickness="1" Padding="16,6">
        <StackPanel x:Name="Steps"/>
      </Border>

      <StackPanel Grid.Row="4" Margin="0,18,6,0">
        <Border x:Name="Track" Height="6" CornerRadius="3" Background="$($c.Fill)" ClipToBounds="True">
          <Border x:Name="Bar" CornerRadius="3" Background="$($c.Tint)" HorizontalAlignment="Left" Width="0"/>
        </Border>
        <Grid Margin="0,10,0,0">
          <Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
          <TextBlock x:Name="Status" FontWeight="SemiBold" TextTrimming="CharacterEllipsis" Text="Getting ready"/>
          <TextBlock x:Name="Percent" Grid.Column="1" Margin="12,0,0,0" Foreground="$($c.Dim)"/>
        </Grid>
        <TextBlock x:Name="Sub" Margin="0,2,0,0" FontSize="12" Foreground="$($c.Dim)" TextWrapping="Wrap" MaxHeight="64"/>
      </StackPanel>

      <StackPanel Grid.Row="5" Margin="0,12,6,0">
        <TextBlock x:Name="DetailsToggle" Text="Show details" FontSize="12" Foreground="$($c.Tint)" Cursor="Hand" HorizontalAlignment="Left"/>
        <Border x:Name="DetailsBox" Visibility="Collapsed" Margin="0,8,0,0" CornerRadius="10" Background="$($c.Card)" BorderBrush="$($c.Line)" BorderThickness="1" Padding="4">
          <TextBox x:Name="Details" Height="150" IsReadOnly="True" BorderThickness="0" Background="Transparent" Foreground="$($c.Dim)"
                   FontFamily="Cascadia Mono, Consolas" FontSize="11" TextWrapping="NoWrap"
                   VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Auto"/>
        </Border>
      </StackPanel>

      <StackPanel Grid.Row="6" Margin="0,18,6,0" Orientation="Horizontal" HorizontalAlignment="Right">
        <Button x:Name="Secondary" Style="{StaticResource Btn}" Content="Cancel"/>
        <Button x:Name="Primary" Style="{StaticResource Primary}" Content="Try again" Visibility="Collapsed"/>
      </StackPanel>
    </Grid>
  </Border>
</Window>
"@
    $window = [Windows.Markup.XamlReader]::Parse($xaml)
    $find = { param($name) $window.FindName($name) }
    $ui = @{}
    foreach ($name in 'Shell', 'Lift', 'Logo', 'MinButton', 'CloseButton', 'Headline', 'Intro', 'Steps', 'Track', 'Bar', 'Status',
                      'Percent', 'Sub', 'DetailsToggle', 'DetailsBox', 'Details', 'Secondary', 'Primary') { $ui[$name] = & $find $name }
    $brush = @{}
    foreach ($k in $c.Keys) { $brush[$k] = New-Object Windows.Media.SolidColorBrush ([Windows.Media.ColorConverter]::ConvertFromString($c[$k])) }

    # ---- The five steps --------------------------------------------------------------------
    $names = 'Download Subtitle Maker', 'Python', 'App packages', 'Graphics card support', 'Finish and open'
    $notes = 'a few MB', '', 'about 300 MB', 'only for NVIDIA cards, about 1 GB', ''
    $rows = @()
    for ($i = 0; $i -lt $names.Count; $i++) {
        $grid = New-Object Windows.Controls.Grid
        $grid.Margin = '0,9,0,9'
        foreach ($w in 30, 1) {
            $col = New-Object Windows.Controls.ColumnDefinition
            $col.Width = if ($w -eq 1) { New-Object Windows.GridLength 1, 'Star' } else { New-Object Windows.GridLength $w }
            $grid.ColumnDefinitions.Add($col)
        }
        $dot = New-Object Windows.Controls.Grid
        $dot.Width = 20; $dot.Height = 20; $dot.HorizontalAlignment = 'Left'
        $ring = New-Object Windows.Shapes.Ellipse
        $ring.Stroke = $brush.Line; $ring.StrokeThickness = 1.6
        $arc = New-Object Windows.Shapes.Ellipse
        $arc.Stroke = $brush.Tint; $arc.StrokeThickness = 2; $arc.StrokeDashArray = New-Object Windows.Media.DoubleCollection (,[double[]](7, 100))
        $arc.StrokeDashCap = 'Round'; $arc.RenderTransformOrigin = '0.5,0.5'; $arc.Visibility = 'Collapsed'
        $spin = New-Object Windows.Media.RotateTransform
        $arc.RenderTransform = $spin
        $fill = New-Object Windows.Shapes.Ellipse
        $fill.Visibility = 'Collapsed'
        $pop = New-Object Windows.Media.ScaleTransform 1, 1
        $dot.RenderTransformOrigin = '0.5,0.5'
        $dot.RenderTransform = $pop
        $glyph = New-Object Windows.Controls.TextBlock
        $glyph.FontFamily = 'Segoe Fluent Icons, Segoe MDL2 Assets'; $glyph.FontSize = 10; $glyph.Foreground = $brush.OnTint
        $glyph.HorizontalAlignment = 'Center'; $glyph.VerticalAlignment = 'Center'
        foreach ($part in $ring, $arc, $fill, $glyph) { [void]$dot.Children.Add($part) }
        $label = New-Object Windows.Controls.TextBlock
        $label.Text = $names[$i]; $label.Foreground = $brush.Dim; $label.VerticalAlignment = 'Center'
        $note = New-Object Windows.Controls.TextBlock
        $note.Text = $notes[$i]; $note.Foreground = $brush.Dim; $note.FontSize = 12; $note.Margin = '10,0,0,0'
        $note.VerticalAlignment = 'Center'; $note.Opacity = 0.75
        $line = New-Object Windows.Controls.StackPanel
        $line.Orientation = 'Horizontal'
        [void]$line.Children.Add($label); [void]$line.Children.Add($note)
        [Windows.Controls.Grid]::SetColumn($line, 1)
        [void]$grid.Children.Add($dot); [void]$grid.Children.Add($line)
        if ($i -gt 0) {
            $rule = New-Object Windows.Controls.Border
            $rule.Height = 1; $rule.Background = $brush.Line; $rule.Margin = '30,0,0,0'; $rule.Opacity = 0.7
            [void]$ui.Steps.Children.Add($rule)
        }
        [void]$ui.Steps.Children.Add($grid)
        $rows += @{ Ring = $ring; Arc = $arc; Spin = $spin; Fill = $fill; Pop = $pop; Glyph = $glyph; Label = $label; Note = $note; Look = '' }
    }

    function Set-Look($row, $look) {
        if ($row.Look -eq $look) { return }
        $row.Look = $look
        $row.Arc.Visibility = if ($look -eq 'active') { 'Visible' } else { 'Collapsed' }
        $row.Ring.Visibility = if ($look -in 'pending', 'active') { 'Visible' } else { 'Collapsed' }
        $row.Fill.Visibility = if ($look -in 'pending', 'active') { 'Collapsed' } else { 'Visible' }
        $row.Label.Foreground = if ($look -eq 'pending') { $brush.Dim } else { $brush.Ink }
        $row.Label.FontWeight = if ($look -eq 'active') { 'SemiBold' } else { 'Normal' }
        switch ($look) {
            'done'    { $row.Fill.Fill = $brush.Ok;    $row.Glyph.Text = [char]0xE73E }
            'skipped' { $row.Fill.Fill = $brush.Faint; $row.Glyph.Text = [char]0xE738 }
            'failed'  { $row.Fill.Fill = $brush.Tint;  $row.Glyph.Text = [char]0xE711 }
            default   { $row.Glyph.Text = '' }
        }
        if ($look -in 'done', 'failed', 'skipped') {
            # A small spring as each step finishes
            $grow = New-Object Windows.Media.Animation.DoubleAnimation 0.4, 1, ([TimeSpan]::FromMilliseconds(420))
            $ease = New-Object Windows.Media.Animation.BackEase
            $ease.Amplitude = 0.45; $ease.EasingMode = 'EaseOut'; $grow.EasingFunction = $ease
            $row.Pop.BeginAnimation([Windows.Media.ScaleTransform]::ScaleXProperty, $grow)
            $row.Pop.BeginAnimation([Windows.Media.ScaleTransform]::ScaleYProperty, $grow)
        }
    }

    # ---- Buttons ---------------------------------------------------------------------------
    $state = @{ Shown = 'working'; Closing = $false; Bar = 0.0; Phase = 0.0; DoneAt = $null; Lines = 0 }
    $fadeOut = {
        if ($state.Closing) { return }
        $state.Closing = $true
        $out = New-Object Windows.Media.Animation.DoubleAnimation 0, ([TimeSpan]::FromMilliseconds(220))
        $out.Add_Completed({ $window.Close() })
        $window.BeginAnimation([Windows.Window]::OpacityProperty, $out)
    }
    $askToStop = {
        $answer = [Windows.MessageBox]::Show($window,
            "Stop installing Subtitle Maker?`n`nWhat's downloaded so far is kept, so running the installer again carries on from here.",
            'Subtitle Maker', 'YesNo', 'Question')
        if ($answer -ne 'Yes') { return }
        $sync.Cancel = $true
        if ($sync.Pid) { & taskkill.exe /T /F /PID $sync.Pid 2>&1 | Out-Null }
        $ui.Status.Text = 'Stopping'
        $ui.Secondary.IsEnabled = $false
    }
    $ui.Secondary.Add_Click({ if ($sync.State -eq 'working') { & $askToStop } else { & $fadeOut } })
    $ui.CloseButton.Add_Click({ if ($sync.State -eq 'working') { & $askToStop } else { & $fadeOut } })
    $ui.MinButton.Add_Click({ $window.WindowState = 'Minimized' })
    $ui.Primary.Add_Click({
        if ($sync.State -eq 'failed') { $ui.Primary.IsEnabled = $false; $sync.Retry = $true } else { & $fadeOut }
    })
    $ui.DetailsToggle.Add_MouseLeftButtonUp({
        $open = $ui.DetailsBox.Visibility -ne 'Visible'
        $ui.DetailsBox.Visibility = if ($open) { 'Visible' } else { 'Collapsed' }
        $ui.DetailsToggle.Text = if ($open) { 'Hide details' } else { 'Show details' }
        if ($open) { $ui.Details.ScrollToEnd() }
    })
    $window.Add_MouseLeftButtonDown({ param($s, $e)
        if ($e.OriginalSource -isnot [Windows.Controls.Primitives.TextBoxBase] -and $e.OriginalSource -ne $ui.DetailsToggle -and $e.ButtonState -eq 'Pressed') { try { $window.DragMove() } catch { } }
    })
    $window.Add_Closing({ param($s, $e)
        if ($sync.State -eq 'working' -and -not $sync.Cancel) { $e.Cancel = $true; & $askToStop }
    })

    # ---- Keeping up with the install, 30 times a second -----------------------------------
    $timer = New-Object Windows.Threading.DispatcherTimer
    $timer.Interval = [TimeSpan]::FromMilliseconds(33)
    $timer.Add_Tick({
        try {
            $line = $null; $added = $false
            while ($sync.Log.TryDequeue([ref]$line)) { $ui.Details.AppendText("$line`r`n"); $added = $true; $state.Lines++ }
            if ($added) {
                if ($ui.Details.Text.Length -gt 300000) { $ui.Details.Text = $ui.Details.Text.Substring(100000) }
                if ($ui.DetailsBox.Visibility -eq 'Visible') { $ui.Details.ScrollToEnd() }
            }

            # A new try (Try again) starts the window over
            if ($state.Shown -eq 'failed' -and $sync.State -eq 'working') {
                $state.Shown = 'working'
                foreach ($r in $rows) { $r.Look = '' }
                $ui.Headline.Text = 'Installing Subtitle Maker'
                $ui.Intro.Visibility = 'Visible'
                $ui.Status.Foreground = $brush.Ink; $ui.Bar.Background = $brush.Tint
                $ui.Primary.Visibility = 'Collapsed'; $ui.Primary.IsEnabled = $true
                $ui.Secondary.Content = 'Cancel'; $ui.Secondary.IsEnabled = $true
            }

            $stage = [int]$sync.Stage
            for ($i = 0; $i -lt $rows.Count; $i++) {
                $look = if ($i -lt $stage) { if ($i -eq 3 -and $sync.Skip3) { 'skipped' } else { 'done' } }
                        elseif ($i -gt $stage) { 'pending' }
                        elseif ($sync.State -eq 'failed') { 'failed' }
                        elseif ($sync.State -in 'done', 'slow') { 'done' }
                        else { 'active' }
                Set-Look $rows[$i] $look
                if ($look -eq 'active') { $rows[$i].Spin.Angle = ($rows[$i].Spin.Angle + 11) % 360 }
            }

            $width = $ui.Track.ActualWidth
            $progress = [double]$sync.Progress
            if ($sync.State -in 'done', 'slow') { $progress = 1 }
            if ($progress -lt 0 -and $sync.State -eq 'working') {
                # Busy, with no way to tell how far: a short bar sliding across
                $state.Phase = ($state.Phase + 0.018) % 1.35
                $segment = $width * 0.28
                $ui.Bar.Width = $segment
                $ui.Bar.Margin = New-Object Windows.Thickness ((($state.Phase / 1.35) * ($width + $segment)) - $segment), 0, 0, 0
                $ui.Percent.Text = ''
                $state.Bar = 0
            } else {
                if ($progress -lt 0) { $progress = $state.Bar }
                $state.Bar += ($progress - $state.Bar) * 0.14   # glide towards it rather than jump
                $ui.Bar.Margin = New-Object Windows.Thickness 0
                $ui.Bar.Width = [Math]::Max(0, $state.Bar * $width)
                $ui.Percent.Text = if (($stage -in @(2, 3)) -and $sync.State -eq 'working' -and $progress -gt 0 -and $progress -lt 1) { '{0:0}%' -f ($progress * 100) } else { '' }
            }

            switch ($sync.State) {
                'working' {
                    if (-not $sync.Cancel) { $ui.Status.Text = $sync.Status }
                    $ui.Sub.Text = $sync.Sub; $ui.Sub.Foreground = $brush.Dim
                }
                'failed' {
                    if ($state.Shown -ne 'failed') {
                        $state.Shown = 'failed'
                        $ui.Headline.Text = "The install didn't finish"
                        $ui.Intro.Visibility = 'Collapsed'
                        $ui.Status.Text = 'Something got in the way'
                        $ui.Status.Foreground = $brush.Tint
                        $why = if ($sync.Error) { $sync.Error } else { 'Show details has what happened.' }
                        $ui.Sub.Text = "$why`nTry again carries on from where it stopped."
                        $ui.Bar.Background = $brush.Tint
                        $ui.Secondary.Content = 'Close'; $ui.Secondary.IsEnabled = $true
                        $ui.Primary.Content = 'Try again'; $ui.Primary.Visibility = 'Visible'
                    }
                }
                { $_ -in 'done', 'slow' } {
                    if ($state.Shown -ne $sync.State) {
                        $state.Shown = $sync.State
                        $ui.Headline.Text = 'Subtitle Maker is installed'
                        $ui.Intro.Visibility = 'Collapsed'
                        $ui.Bar.Background = $brush.Ok
                        $ui.Secondary.Visibility = 'Collapsed'
                        if ($sync.State -eq 'done') {
                            $ui.Status.Text = 'Subtitle Maker is open'
                            $ui.Sub.Text = 'Pick the quality in its window, and it downloads the rest.'
                            $state.DoneAt = Get-Date
                        } else {
                            $ui.Status.Text = 'Its window is taking a while to open'
                            $ui.Sub.Text = 'It will appear by itself. Or open it from the Subtitle Maker shortcut on your desktop.'
                            $ui.Primary.Content = 'Close'; $ui.Primary.Visibility = 'Visible'
                        }
                    }
                    # The app's own window is open now, so this one bows out by itself
                    if ($state.DoneAt -and ((Get-Date) - $state.DoneAt).TotalSeconds -gt 2.2) { & $fadeOut }
                }
                'cancelled' { & $fadeOut }
            }
        } catch {
            $sync.UiError = "$_"
        }
    })

    $window.Add_ContentRendered({
        # The logo as the taskbar icon too
        try {
            $visual = New-Object Windows.Media.DrawingVisual
            $ctx = $visual.RenderOpen()
            $ctx.DrawRectangle((New-Object Windows.Media.VisualBrush $ui.Logo), $null, (New-Object Windows.Rect 0, 0, 64, 64))
            $ctx.Close()
            $bitmap = New-Object Windows.Media.Imaging.RenderTargetBitmap 64, 64, 96, 96, ([Windows.Media.PixelFormats]::Pbgra32)
            $bitmap.Render($visual)
            $window.Icon = $bitmap
        } catch { }
        $ease = New-Object Windows.Media.Animation.CubicEase
        $ease.EasingMode = 'EaseOut'
        $in = New-Object Windows.Media.Animation.DoubleAnimation 1, ([TimeSpan]::FromMilliseconds(260))
        $rise = New-Object Windows.Media.Animation.DoubleAnimation 0, ([TimeSpan]::FromMilliseconds(420))
        $rise.EasingFunction = $ease
        $window.BeginAnimation([Windows.Window]::OpacityProperty, $in)
        $ui.Lift.BeginAnimation([Windows.Media.TranslateTransform]::YProperty, $rise)
        $timer.Start()
        $window.Activate() | Out-Null
        $sync.Shown = $true
    })
    $window.Add_Closed({ $timer.Stop(); $sync.Closed = $true })
    [void]$window.ShowDialog()
} catch {
    $sync.UiError = "$_"
} finally {
    $sync.Closed = $true
}
'@

function Start-SubtitleMakerWindow {
    $sync = [hashtable]::Synchronized(@{
        Log = New-Object 'System.Collections.Concurrent.ConcurrentQueue[string]'
        Stage = 0; Status = 'Getting ready'; Sub = ''; Progress = -1; State = 'working'; Error = ''; Skip3 = $false
        Cancel = $false; Retry = $false; Pid = 0; Shown = $false; Closed = $false; UiError = $null
    })
    try {
        $runspace = [runspacefactory]::CreateRunspace()
        $runspace.ApartmentState = 'STA'
        $runspace.ThreadOptions = 'ReuseThread'
        $runspace.Open()
        $shell = [powershell]::Create()
        $shell.Runspace = $runspace
        [void]$shell.AddScript($script:SubtitleMakerWindow).AddArgument($sync)
        [void]$shell.BeginInvoke()
    } catch { return $null }
    $deadline = (Get-Date).AddSeconds(20)
    while (-not $sync.Shown -and -not $sync.Closed -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (-not $sync.Shown) { return $null }
    return $sync
}

function Install-SubtitleMakerWithWindow {
    $sync = Start-SubtitleMakerWindow
    if (-not $sync) {
        # No window on this PC (very rare): install anyway, and say how it went in a message box
        $ok = Install-SubtitleMakerSafely
        Add-Type -AssemblyName System.Windows.Forms
        $text = if ($ok) { 'Subtitle Maker is installed. Open it from the shortcut on your desktop.' } else { 'The install did not finish. Check your internet connection and run the installer again.' }
        [void][System.Windows.Forms.MessageBox]::Show($text, 'Subtitle Maker')
        return $ok
    }
    $script:SubtitleMakerUi = $sync
    while ($true) {
        $ok = Install-SubtitleMakerSafely
        if ($sync.Cancel -or $sync.Closed) { $sync.State = 'cancelled'; break }
        if ($ok) { if ($sync.State -eq 'working') { $sync.State = 'done' }; break }
        $sync.State = 'failed'
        while (-not $sync.Retry -and -not $sync.Closed) { Start-Sleep -Milliseconds 200 }
        if (-not $sync.Retry) { break }
        $sync.Retry = $false; $sync.Error = ''; $sync.Skip3 = $false
        $sync.Stage = 0; $sync.Progress = -1; $sync.Status = 'Trying again'; $sync.Sub = ''
        $sync.State = 'working'
    }
    while (-not $sync.Closed) { Start-Sleep -Milliseconds 200 }   # it closes itself once the app is open
    $script:SubtitleMakerUi = $null
    return $ok
}

# A function (not the script itself) does the work, so a problem never closes the PowerShell
# window it was pasted into; the Windows tests read the result from this variable
$global:SubtitleMakerInstalled = if ($env:SUBTITLE_MAKER_GUI -eq '1') { Install-SubtitleMakerWithWindow } else { Install-SubtitleMakerSafely }
