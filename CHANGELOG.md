# What's new

Each version's changes, newest first. The app reads this file: the first version below is the
installed version's number, and its list is what the What's new sheet shows after an update.
A release (see "Releasing a new version" in the README) needs its section here first.

## 0.2.1 · 31 October 2026

- **Safer updates.** An update now goes in all at once. If something gets in the way partway, the old version is put back, instead of leaving a mix of old and new files.
- **Safer downloads.** The speech and speaker models are checked to be exactly the right files before they're used.
- **Works offline during a security update.** If an important security update is waiting and GitHub can't be reached, you can keep using Subtitle Maker until you're back online.
- **Asks before closing** if a video is still being made, models are downloading, or an update is installing.
- **Says when an .srt's formatting won't be kept.** Opening one with italics, colours or positions explains that saving keeps the words and timing, but not the formatting.
- Big updates say how many more changes there are, beyond the ten listed.
- Uninstall now moves every voice clip to the desktop, including ones with % in the name.

## 0.2.0 · 29 October 2026

- **No more freezing on long videos.** Transcribing used to stall partway through while it worked out who was talking. Now both run at full speed side by side.
- **Videos in Google Drive** (or OneDrive, Dropbox, a network drive) are copied to your PC first, with a counter on screen, instead of being read over the internet bit by bit.
- **Much better speaker detection.** It goes sentence by sentence with a better voice model, and remembers people you've named, so their next video names them by itself (Settings > Remembered voices).
- **Fix any .srt.** Open one, play each line, edit the words, split and join lines, nudge the timing, and save. The old file is kept as a backup.
- **Check the lines before they're saved**, and set up word fixes that correct the same mistakes in every video.
- **Videos after the first start right away**, since the speech model stays loaded.
- **Whole folders**, .vtt files, retrying failed videos, and carrying on with a queue that didn't finish.
- **Progress you can trust.** The bar moves steadily, 100% means done, and the time left is much closer.
- **Progress on the taskbar button**, and a notification when the videos are done.
- **Experimental version:** try new features first, and go back to Stable any time (Settings > Updates).
- **Important security updates** have to be installed before the app can be used again.
- **New animations from IronLog.** Sheets slide up and can be dragged away, screens slide in and out, and Settings > Less animations turns them into simple fades.
- More settings, with a live preview of how the subtitles split.

## 0.1.1 · 27 October 2026

- Installs with no security warnings, and has its own icon.
- Setup and update screens in the window.
- Models are no longer downloaded again when they're already there.
- Starts faster on the processor, and shows usage meters in Potato mode.

## 0.1.0 · 27 October 2026

- The first version: movie-style .srt subtitles for DaVinci Resolve from any video, with speaker names, on the graphics card or the processor.
