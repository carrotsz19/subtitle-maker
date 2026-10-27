SUBTITLE MAKER
==============
Turns the talking in a video into subtitles (.srt) you can import into DaVinci Resolve.
Runs on your own PC. No account, no uploading.


FIRST TIME ON A PC (once)
-------------------------
1. Double-click  Setup.bat
2. It checks your PC and asks two questions. Just press Enter to take the
   recommended answer:
     - Quality level (see below)
     - The language spoken in your videos (English, auto-detect, or another one)
3. Wait until it says "All set" (5-10 minutes, needs internet).

That's it. After this it works offline.

Got an NVIDIA graphics card? Install its driver first (nvidia.com/drivers or the
NVIDIA App), then run Setup.bat. It'll be about 3x faster. If you installed the
driver later, just run Setup.bat again.


QUALITY LEVELS
--------------
Potato     quickest, for old or slow PCs. Only makes subtitles: no speaker names.
Fast       quick, decent accuracy.
Balanced   good accuracy. On a GTX 1050 it ran slower than High.
High       best accuracy for its speed. Recommended for most PCs, including a
           GTX 1050 or better.
Maximum    the most accurate, and the slowest. Needs a 3 GB+ graphics card or patience.

To change the level or language later, use Settings in the window, or double-click  Settings.bat


THE EASY WAY: THE WINDOW
------------------------
Double-click  Subtitle Maker.bat
1. Drop one or more videos onto the window (or click "Choose files...").
2. Pick "One person" or "Several people" (and how many, or Guess).
3. Click "Make subtitles". You see the progress, the time left, and how hard
   the graphics card, CPU and memory are working.
4. With several people, it plays a few seconds of each voice it can't name,
   shows their longest lines, and you type who it is.
5. When it's done, click "Show in folder".
Under "Save subtitles to" you can pick a folder for all your subtitles instead of
next to each video (the drag-onto-icon .bat files use that folder too).
History lists everything you've made, with Show in folder and Make again.
Settings (quality and language) and Resolve tips are in the sidebar.

The two drag-onto-icon .bat files below still work the same as before.


MAKING SUBTITLES (one person talking)
-------------------------------------
1. Drag your video onto  Make Subtitles (drop video here).bat
2. Wait for the progress bar to finish.
3. It beeps, and the folder opens with the new .srt picked out. It has the same
   name as your video. The window title shows how far along it is.

It never overwrites anything: if myvideo.srt is already there, you get
myvideo (2).srt instead.


MAKING SUBTITLES (two or more people talking)
---------------------------------------------
1. (Optional, but best) Put a short voice clip of each person in the Voices folder:
     Voices\Alex.wav
     Voices\Jamie.mp3
   10-20 seconds of them talking ALONE. The file name is the name shown in the subtitles.

2. Drag your video onto  Make Subtitles - multi speaker (drop video here).bat
3. It asks "How many people talk?" Type the number, or just press Enter.
4. If it can't tell who someone is, it plays a few seconds of their voice, shows
   how long they talk and their 3 longest lines, and asks for their name.
   Type P and Enter to hear the clip again, or just Enter to keep "Speaker 1".

You get:
   myvideo.srt           every line, with names     ("Alex: Where are we going?")
   myvideo - Alex.srt    only Alex's lines
   myvideo - Jamie.srt   only Jamie's lines


PUTTING THEM IN DAVINCI RESOLVE
-------------------------------
1. File > Import > Subtitle, pick the .srt
2. Drag it from the Media Pool onto the timeline. It makes a subtitle track.
3. Line it up with the START of the timeline (if you used your exported video).
4. To style them: click any subtitle > Inspector > Track tab. Changes apply to all of them.

Two people on screen at once: Resolve only shows ONE subtitle track at a time,
so use regions instead. Put one person's .srt on the track (region R1), right-click
INSIDE the subtitle track (not its header) > Add Subtitle Region, and drag the
other person's .srt into R2.
Each region can have its own position and colour (Inspector > Track).


TIPS FOR BETTER RESULTS
-----------------------
- Use your exported video, so the timings match your whole timeline.
- Speech under loud music gets missed. Export a version with the music muted first.
- Close DaVinci Resolve and games while it runs, so it can use the graphics card.
- Fix any wrong words in Resolve (click a subtitle > Inspector > Caption).
- Speaker names are usually right, but check short lines like "yeah" or "okay".
- Speaker names need the Fast level or higher (Potato turns them off).


COPYING TO ANOTHER PC
---------------------
Copy this whole Tools folder anywhere and run Setup.bat on the new PC.
The big stuff (about 3 GB of programs and models) is stored on each PC in
   C:\Users\<you>\.subtitle-maker
not in this folder, so this folder stays small and Google Drive doesn't sync it.
To remove everything from a PC, use Uninstall (see below).

Mac / Linux: open Terminal in this folder and run
   bash setup.sh                                  (once)
   bash app.sh                                    (the window)
   bash subtitles.sh "myvideo.mp4"                (one person)
   bash subtitles.sh "myvideo.mp4" --speakers 2   (two people; 0 = guess)
   bash settings.sh                               (change quality or language)


INSTALLING ON A NEW PC, AND UPDATES
-----------------------------------
Subtitle Maker lives on GitHub: github.com/carrotsz19/subtitle-maker
On a new PC, download  Install Subtitle Maker.bat  from there and double-click it.
It downloads the latest version into  C:\Users\<you>\Subtitle Maker , runs Setup,
and puts a "Subtitle Maker" shortcut on the desktop. Run it again any time to repair.
(Windows may say "Windows protected your PC" for a downloaded .bat: click
"More info", then "Run anyway".)

To update: Settings > Updates in the window, or double-click  Update.bat
Your Voices clips, settings and models are kept.

No GitHub account is needed for installing or updating.

To uninstall: Settings > "Uninstall Subtitle Maker..." in the window, or Uninstall.bat.
It removes the Python packages, models, settings, program files and desktop shortcut.
Your videos and .srt files are never touched, and your voice clips are moved to the
desktop unless you untick that.


IF SOMETHING GOES WRONG
-----------------------
"First time on this PC: run Setup.bat"   -> run Setup.bat.
"Using CPU instead"                       -> another app is using the graphics card.
                                             Close Resolve/games, or just wait (it's slower).
"The GPU ran out of memory"               -> same, but it happened partway. It carries on
                                             with the CPU by itself.
"No speech found"                         -> the file has no clear talking in it.
"Couldn't read the sound in ..."          -> it isn't a video or audio file, or it's damaged.
                                             Export it again as .mp4 or .wav.
Setup failed                              -> check your internet and run Setup.bat again.
"Something went wrong"                    -> the details are saved in
                                             C:\Users\<you>\.subtitle-maker\last_error.log
                                             Send that file along if you ask for help.


WHAT'S IN THIS FOLDER
---------------------
Subtitle Maker.bat                              opens the window (easiest)
Install Subtitle Maker.bat                      installs or repairs it on a new PC
Update.bat                                      gets the latest version from GitHub
Uninstall.bat                                   removes it from this PC
Setup.bat                                       installs everything (run once per PC)
Settings.bat                                    change quality level or language
Make Subtitles (drop video here).bat            one-person subtitles
Make Subtitles - multi speaker (...).bat        subtitles with names
Voices\                                         voice clips for naming people
make_subtitles.py, speakers.py, paths.py,
configure.py, app.py, update.py, uninstall.py,
ui\                                             the actual program
requirements*.txt                               list of what Setup installs
setup.sh, subtitles.sh, settings.sh, app.sh     Mac / Linux versions
