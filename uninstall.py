"""Removes Subtitle Maker from this PC (Settings > Uninstall in the window, or Uninstall.bat).

Only what Subtitle Maker itself made is deleted: its Python environment, models and settings,
the program files the installer put in place, and the desktop shortcut. Folders are removed
only once they are empty, so anything else in them stays. Videos and .srt files are never
touched, and voice clips are moved to the desktop unless you say otherwise.

Python can't delete the environment it is running from, so the actual removal is done by a
small script that waits for this program (and the window) to close first.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import update
from paths import data_dir

HERE = Path(__file__).parent.resolve()
# What Subtitle Maker keeps in its data folder; nothing else there is touched
DATA_DIRS = ("venv", "models", "webview", "backups")
DATA_FILES = ("settings.json", "history.json", "installed.json", "install-sha.txt", "install-files.txt",
              "last_error.log", "startup.log", "queue.json", "update-packages.txt", "speed.json",
              "known_voices.json")


def desktop():
    if sys.platform == "win32":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                                 capture_output=True, text=True, timeout=20,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
            if out:
                return Path(out)
        except (OSError, subprocess.SubprocessError):
            pass
    return Path.home() / "Desktop"


def folder_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def plan():
    """What uninstalling would remove, for the confirmation screen."""
    data = data_dir()
    version = update.installed()
    # The program folder only goes if the installer made it; a copy elsewhere (say, Google Drive) stays
    program = HERE if version and (HERE / "make_subtitles.py").exists() else None
    shortcut = desktop() / "Subtitle Maker.lnk"
    voices = [p.name for p in (HERE / "Voices").iterdir()
              if p.is_file() and p.name != "README.txt"] if (HERE / "Voices").is_dir() else []
    return {
        "data": str(data),
        "data_gb": round(sum(folder_size(data / d) for d in DATA_DIRS if (data / d).is_dir()) / 2**30, 1),
        "program": str(program) if program else None,
        "program_files": version["files"] if program else [],
        "shortcut": str(shortcut) if shortcut.exists() else None,
        "voices": voices if program else [],  # only at risk when the program folder goes
        "voices_to": str(desktop() / "Subtitle Maker voice clips"),
    }


def script(p, keep_voices, wait_pid):
    """The commands that do the removal, as a Windows batch file or a POSIX shell script."""
    data = Path(p["data"])
    program = Path(p["program"]) if p["program"] else None
    win = sys.platform == "win32"
    q = (lambda s: f'"{s}"') if win else (lambda s: "'" + str(s).replace("'", "'\\''") + "'")
    lines = []
    if win:
        lines += ["@echo off", "title Uninstalling Subtitle Maker", "echo Waiting for Subtitle Maker to close...",
                  ":wait", f'tasklist /FI "PID eq {wait_pid}" 2>nul | find "{wait_pid}" >nul && '
                           f'(timeout /t 1 /nobreak >nul & goto wait)', "echo Removing Subtitle Maker..."]
        rmtree, rm, rmdir = "rmdir /s /q {} 2>nul", "del /f /q {} 2>nul", "rmdir {} 2>nul"
    else:
        lines += ["#!/bin/sh", "echo 'Waiting for Subtitle Maker to close...'",
                  f"while kill -0 {wait_pid} 2>/dev/null; do sleep 1; done", "echo 'Removing Subtitle Maker...'"]
        rmtree, rm, rmdir = "rm -rf {}", "rm -f {}", "rmdir {} 2>/dev/null"

    for d in DATA_DIRS:
        lines.append(rmtree.format(q(data / d)))
        # A file still in use (antivirus scanning, a program just closing) stops the first try
        # partway, which once left a half-deleted Python behind; wait a moment and try again
        if win:
            lines.append(f"if exist {q(data / d)} (timeout /t 3 /nobreak >nul & {rmtree.format(q(data / d))})")
        else:
            lines.append(f"[ -e {q(data / d)} ] && sleep 3 && {rmtree.format(q(data / d))}")
    for f in DATA_FILES:
        lines.append(rm.format(q(data / f)))
    if program:
        if keep_voices and p["voices"]:
            dest = Path(p["voices_to"])
            if win:
                lines.append(f"mkdir {q(dest)} 2>nul")
                lines += [f"move /y {q(program / 'Voices' / v)} {q(dest)} >nul" for v in p["voices"]]
            else:
                lines.append(f"mkdir -p {q(dest)}")
                lines += [f"mv -f {q(program / 'Voices' / v)} {q(dest)}/" for v in p["voices"]]
        elif p["voices"]:
            lines += [rm.format(q(program / "Voices" / v)) for v in p["voices"]]
        for rel in p["program_files"]:
            lines.append(rm.format(q(program / rel)))
        # __pycache__ is Python's own; then each folder, deepest first, only if now empty
        folders = {program / Path(rel).parent for rel in p["program_files"]} | {program / "ui", program / "Voices"}
        lines.append(rmtree.format(q(program / "__pycache__")))
        for folder in sorted(folders, key=lambda f: len(f.parts), reverse=True):
            if folder != program:
                lines.append(rmdir.format(q(folder)))
        lines.append(rmdir.format(q(program)))
    if p["shortcut"]:
        lines.append(rm.format(q(p["shortcut"])))
    lines.append(rmdir.format(q(data)))  # only if nothing else of yours is in it

    # Say so if something couldn't be removed, rather than leaving it behind silently
    if win:
        lines += [f"if exist {q(data / 'venv')} (echo. & echo Some files were in use and couldn't be removed: {data}"
                  " & echo Restart the PC and delete that folder by hand. & pause)"]
    else:
        lines += [f"[ -e {q(data / 'venv')} ] && echo \"Some files were in use and couldn't be removed: {data}\""]
    if win:
        lines += ["echo.", "echo Subtitle Maker has been removed. Your videos and subtitles were not touched.",
                  "timeout /t 6 >nul", '(goto) 2>nul & del "%~f0"']
    else:
        lines += ["echo 'Subtitle Maker has been removed. Your videos and subtitles were not touched.'", 'rm -f "$0"']
    return "\r\n".join(lines) + "\r\n" if win else "\n".join(lines) + "\n"


def start(keep_voices=True, wait_pid=None):
    """Write the removal script and start it; it waits for wait_pid (default: this program) to exit."""
    p = plan()
    suffix = ".bat" if sys.platform == "win32" else ".sh"
    fd, path = tempfile.mkstemp(prefix="uninstall-subtitle-maker-", suffix=suffix)
    with os.fdopen(fd, "w", newline="") as f:
        f.write(script(p, keep_voices, wait_pid or os.getpid()))
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", path], cwd=tempfile.gettempdir(),
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        subprocess.Popen(["sh", path], cwd=tempfile.gettempdir(), start_new_session=True)
    return p


def main():
    p = plan()
    print("This removes Subtitle Maker from this PC:")
    print(f"  - its Python packages and models ({p['data_gb']} GB) in {p['data']}")
    if p["program"]:
        print(f"  - the program files in {p['program']}")
    if p["shortcut"]:
        print("  - the desktop shortcut")
    print("Your videos and .srt files are not touched.")
    keep = True
    if p["voices"]:
        keep = input(f"Keep your {len(p['voices'])} voice clips (moved to {p['voices_to']})? [Y/n] ").strip().lower() \
            in ("", "y", "yes")
    if input("Uninstall now? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing was removed.")
        return
    start(keep)
    print("Removing... this window can close.")


if __name__ == "__main__":
    main()
