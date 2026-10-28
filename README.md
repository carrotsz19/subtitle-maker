# Subtitle Maker

Turns the talking in a video into movie-style subtitles (.srt) for DaVinci Resolve,
on your own PC. Optional speaker detection labels who is talking.

**New PC:** open PowerShell (right-click Start > Terminal), paste this and press Enter:

```powershell
irm https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1 | iex
```

It downloads the latest version to `%USERPROFILE%\Subtitle Maker`, installs Python and
everything else, puts a shortcut on the desktop and opens the setup window. No security
warnings, because nothing is downloaded through the browser. (Prefer double-clicking?
`Install Subtitle Maker.bat` does the same, after the usual warnings for downloaded files.)

**Updating:** Settings > Updates in the window, or `Update.bat`.

**Experimental version:** newer features before they're fully tested. Switch in Settings > Updates
(Version to follow: Experimental), and back to Stable the same way. To install it fresh:

    $env:SUBTITLE_MAKER_BRANCH = 'experimental'; irm https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1 | iex

**Fixing subtitles:** every video gets a check screen before saving (fix words, names and timing,
split or join lines). Drop any .srt on the window, or use "Fix an existing .srt…" or History, to fix
one later; the old version is kept in a backups folder. Settings > Word fixes corrects words it
always gets wrong, in every video.

**Several people:** pick "Several people" (and how many, if you know) and each line is labelled with
who said it. People you name are remembered (on this PC only), so later videos name them by
themselves; Settings > Remembered voices lists and forgets them.

**Uninstalling:** Settings > Uninstall in the window, or `Uninstall.bat`.

The full guide is in [README.txt](README.txt).
