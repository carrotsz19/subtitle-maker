# Subtitle Maker

Turns the talking in a video into movie-style subtitles (.srt) for DaVinci Resolve,
on your own PC. Optional speaker detection labels who is talking.

**New PC:** download `Subtitle-Maker-Setup.exe` from the
[latest release](https://github.com/carrotsz19/subtitle-maker/releases/latest) and double-click it.
It isn't signed yet, so Windows says "Windows protected your PC": click **More info**, then
**Run anyway**. The installer window installs it into `%USERPROFILE%\Subtitle Maker`, with Python
and everything else, puts a shortcut on the desktop and opens the app.

Or, with no download warnings at all: open PowerShell (right-click Start > Terminal), paste this and
press Enter. It does the same in the console, with the newest version of `main`:

```powershell
irm https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1 | iex
```

**Updating:** Settings > Software update in the window, or `Update.bat`.

**Experimental version:** newer features before they're fully tested. Switch in Settings > Software update
(Version to follow: Experimental), and back to Stable the same way. To install it fresh:

    $env:SUBTITLE_MAKER_BRANCH = 'experimental'; irm https://raw.githubusercontent.com/carrotsz19/subtitle-maker/main/install.ps1 | iex

**Fixing subtitles:** every video gets a check screen before saving (fix words, names and timing,
split or join lines). Drop any .srt on the window, or use "Fix an existing .srt…" or History, to fix
one later; the old version is kept in a backups folder. Settings > Accuracy > Word fixes corrects words it
always gets wrong, in every video.

**Several people:** pick "Several people" (and how many, if you know) and each line is labelled with
who said it. People you name are remembered (on this PC only), so later videos name them by
themselves; Settings > Speakers lists and forgets them. With several videos queued, they're all
transcribed first and checked afterwards, one after another, so nobody has to wait by the PC:
people are named in each check (the play button next to a name plays their voice), and anyone
named in one check is named by themselves in the next.

**Uninstalling:** Settings > Uninstall Subtitle Maker in the window, or `Uninstall.bat`.

The full guide is in [README.txt](README.txt).

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by [SignPath Foundation](https://signpath.org).

Only `Subtitle-Maker-Setup.exe`, built from this repository by the Release workflow, is signed. The
open-source libraries it installs (from PyPI) are their projects' own files, as they publish them.

**Team roles**

- Committers and reviewers: [carrotsz19](https://github.com/carrotsz19)
- Approvers: [carrotsz19](https://github.com/carrotsz19)

Changes from anyone else (pull requests) are reviewed by a committer before they're merged, and
every release is approved by hand before it's signed.

**Privacy policy**

This program will not transfer any information to other networked systems unless specifically
requested by the user or the person installing or operating it. Your videos, subtitles, settings
and remembered voices stay on your PC. It only downloads things, and sends nothing about you:

- When installing: Python from [python.org](https://www.python.org) and its open-source packages
  from [PyPI](https://pypi.org) ([PyPI's privacy notice](https://policies.python.org/pypi.org/Privacy-Notice/)).
- The speech models, the first time a quality level is used, from
  [Hugging Face](https://huggingface.co) ([privacy policy](https://huggingface.co/privacy)), and the
  speaker detection models from GitHub.
- Updates: it checks this repository on GitHub for a newer version, and downloads it when you
  update ([GitHub's privacy statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement)).

## License

MIT: see [LICENSE](LICENSE). The speech models (OpenAI Whisper, via faster-whisper) are MIT
licensed; the speaker models are pyannote segmentation 3.0 (MIT), and WeSpeaker and NVIDIA
NeMo TitaNet voice models, downloaded from the sherpa-onnx project's releases.

## Releasing updates

Installed copies follow `main` (or `experimental`, if they chose it). The start of each commit
message decides how an update is announced in the app:

| Commit message starts with | What people see |
|---|---|
| `Critical:` | **Locks older versions** until they update: a window that can't be closed. Only for security problems serious enough to stop people using it. |
| `Security:` | The update window opens by itself (it can be closed). |
| `Hotfix:` or `Fix:` | A banner: an important fix is ready. |
| `Feature:` | A banner: new features are ready. |
| anything else | Shown in Settings > Software update only. |

Push to `experimental` first and let the Windows tests pass (Actions tab) before bringing `main` up to it.

### Releasing a new version

1. Add a section to the top of [CHANGELOG.md](CHANGELOG.md): `## 1.0.0 · 11 November 2026`, then a
   `- ` line per change, written for the people using it. The app shows this version number in
   Settings > Software update, and after updating it opens a What's new sheet with this list (once).
2. Bring `main` up to date with it (as above).
3. Actions tab > Release > Run workflow on `main`, with the version (`v1.0.0`). It tags `main`
   and makes the GitHub release, with the CHANGELOG section at the top of its notes and the
   installer attached. It stops if the CHANGELOG has no section for that version. From v1.0.0 a
   release is a full one; v0.x versions, and ones with a dash (`v1.1.0-beta.1`), are pre-releases.

The installer is `Subtitle-Maker-Setup.exe`, built by the Release workflow with
[Inno Setup](https://jrsoftware.org/isinfo.php) from [installer/SubtitleMaker.iss](installer/SubtitleMaker.iss).
It has the program's files inside it and runs `install.ps1` from them in the installer window, so it
downloads nothing but Python and its packages. (It replaced a .bat that downloaded and ran
`install.ps1`, which antivirus took for a trojan dropper.)
