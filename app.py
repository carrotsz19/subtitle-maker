"""The Subtitle Maker window: drop videos in, watch progress, name speakers, change settings.

It runs make_subtitles.py --events in the background for each file and shows what it reports,
so the window stays responsive, Cancel can stop a run cleanly, and the GPU memory is freed
after every video. Start it with "Subtitle Maker.bat" (or app.sh on Mac/Linux).
"""

import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

import psutil
import webview
from webview.dom import DOMEventHandler

import configure
import models
import uninstall
import update
from paths import (ERROR_LOG, LEVELS, SETTINGS_FILE, data_dir, load_history, load_settings, save_history,
                   save_settings)

HERE = Path(__file__).parent
VOICES_DIR = HERE / "Voices"
EVENT_PREFIX = "@@"  # matches make_subtitles.EVENT_PREFIX
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".mov"}  # matches speakers.AUDIO_EXTS
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def console_python():
    """python.exe next to pythonw.exe, so the background runs can print to a pipe on Windows."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return sys.executable


def open_in_file_manager(path, select=False):
    path = Path(path)
    if sys.platform == "win32":
        if select:
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if select else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if select else path)])


class Api:
    """Everything the page can ask for. Each method is window.pywebview.api.<name>() in the page."""

    def __init__(self):
        self.window = None
        self.process = None
        self.lock = threading.Lock()

    # ---- talking to the page ----------------------------------------------------------------

    def send(self, event):
        if self.window:
            self.window.evaluate_js(f"app.onEvent({json.dumps(event)})")
            if event["type"] == "progress" and event.get("total"):
                self.window.set_title(f"{event['done'] / event['total']:.0%} · Subtitle Maker")

    def on_drop(self, event):
        files = event.get("dataTransfer", {}).get("files", [])
        paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
        if paths:
            self.window.evaluate_js(f"app.addFiles({json.dumps(paths)})")

    # ---- state ------------------------------------------------------------------------------

    def init(self):
        gpu = configure.nvidia_gpu()
        ram_gb = psutil.virtual_memory().total / 2**30
        cores = psutil.cpu_count(logical=False) or os.cpu_count() or 2
        return {
            "settings": load_settings(),
            "configured": SETTINGS_FILE.exists(),
            "levels": LEVELS,
            "recommended": configure.recommend(gpu, ram_gb, cores),
            "hardware": {"gpu": gpu[0] if gpu else None, "vram_mb": gpu[1] if gpu else None,
                         "ram_gb": round(ram_gb), "cores": cores},
            "voices": self.voices(),
            "platform": sys.platform,
            "version": update.installed(),
            "models": self.models_status(),
            # Setup.bat opens the window with --setup; Update.bat with --update
            "mode": "setup" if "--setup" in sys.argv else "update" if "--update" in sys.argv else None,
        }

    def models_status(self):
        """Which speech models are downloaded, per quality level, and whether the speaker models are."""
        return {"whisper": {key: bool(models.whisper_path(lv["model"])) for key, lv in LEVELS.items()},
                "speakers": models.speakers_ready()}

    def voices(self):
        if not VOICES_DIR.is_dir():
            return []
        return [p.stem for p in sorted(VOICES_DIR.iterdir()) if p.suffix.lower() in AUDIO_EXTS]

    def choose_files(self):
        kinds = ("Video and audio (*.mp4;*.mov;*.mkv;*.avi;*.m4v;*.webm;*.wav;*.mp3;*.m4a;*.flac;*.ogg;*.aac)",
                 "All files (*.*)")
        picked = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True, file_types=kinds)
        return list(picked or [])

    # ---- running make_subtitles.py ------------------------------------------------------------

    def start(self, path, speakers=None):
        """Make subtitles for one file. Progress arrives in the page as events."""
        args = [str(Path(path).absolute())]  # the run starts in the Tools folder
        if speakers is not None:
            args += ["--speakers", str(int(speakers))]
        return self.run(args)

    def prepare(self):
        """Download what the saved settings need (after Settings changes)."""
        return self.run(["--prepare"])

    def run(self, args):
        with self.lock:
            if self.process and self.process.poll() is None:
                return False
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
                   "PYTHONIOENCODING": "utf-8"}
            self.process = subprocess.Popen(
                [console_python(), str(HERE / "make_subtitles.py"), *args, "--events"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                cwd=str(HERE), env=env, creationflags=NO_WINDOW,
            )
        threading.Thread(target=self.follow, args=(self.process,), daemon=True).start()
        return True

    def follow(self, process):
        """Pass the run's events to the page, then report how it ended."""
        errors = deque(maxlen=30)
        stderr_thread = threading.Thread(target=lambda: errors.extend(process.stderr), daemon=True)
        stderr_thread.start()
        reported = False
        for line in process.stdout:
            line = line.rstrip("\n")
            if line.startswith(EVENT_PREFIX):
                event = json.loads(line[len(EVENT_PREFIX):])
                reported |= event["type"] in ("done", "error")
                if event["type"] == "done" and sys.platform == "win32":
                    import winsound

                    winsound.MessageBeep()
                self.send(event)
            elif line.strip():
                self.send({"type": "log", "text": line})
        code = process.wait()
        stderr_thread.join(timeout=2)
        if code and not reported and not getattr(process, "cancelled", False):
            tail = "".join(errors).strip().splitlines()
            self.send({"type": "error", "message": tail[-1] if tail else f"It stopped unexpectedly (code {code}).",
                       "log": None})
        if self.window:
            self.window.set_title("Subtitle Maker")
        self.send({"type": "exit", "code": code, "cancelled": getattr(process, "cancelled", False)})

    def answer(self, name):
        """The name typed for the speaker the run is asking about."""
        if self.process and self.process.poll() is None:
            self.process.stdin.write(name.replace("\n", " ") + "\n")
            self.process.stdin.flush()

    def cancel(self):
        if self.process and self.process.poll() is None:
            self.process.cancelled = True
            self.process.kill()

    # ---- settings and folders ---------------------------------------------------------------

    def save_settings(self, level, language):
        settings = load_settings()
        settings.update(level=level, language=language or None)
        save_settings(settings)
        return settings

    # ---- where subtitles go ------------------------------------------------------------------

    def choose_folder(self):
        picked = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        return picked[0] if picked else None

    def set_output_dir(self, folder):
        """None = next to each video. Saved straight away; no download needed."""
        settings = load_settings()
        settings["output_dir"] = folder or None
        save_settings(settings)
        return settings

    # ---- history ----------------------------------------------------------------------------

    def history(self):
        items = load_history()
        for item in items:
            item["input_exists"] = Path(item["input"]).is_file()
            for f in item.get("files", []):
                f["exists"] = Path(f["path"]).is_file()
        return items

    def remove_history(self, entry_id):
        save_history([i for i in load_history() if i.get("id") != entry_id])
        return self.history()

    def clear_history(self):
        save_history([])
        return []

    # ---- updates ----------------------------------------------------------------------------

    def check_update(self):
        try:
            return update.check()
        except update.UpdateError as e:
            return {"error": str(e)}

    def install_update(self, latest):
        """Install that version in the background; the page hears update_progress, then update_done."""
        def report(step, done=None, total=None):
            self.send({"type": "update_progress", "step": step, "done": done, "total": total})

        def work():
            try:
                # Packages this window has loaded are locked on Windows; finish() installs them after it closes
                self.pending_packages = update.apply(latest, report, packages=False)
                self.send({"type": "update_done", "packages": bool(self.pending_packages)})
            except Exception as e:
                self.send({"type": "update_done", "error": f"The update didn't finish: {e}"})
        if self.process and self.process.poll() is None:
            return False
        threading.Thread(target=work, daemon=True).start()
        return True

    def restart(self):
        """Close this window and open the new version (after installing any new packages)."""
        # A visible console only when there are packages to install, so the wait isn't a mystery
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if getattr(self, "pending_packages", None) else NO_WINDOW
        subprocess.Popen([console_python(), str(HERE / "update.py"), "--finish", str(os.getpid())],
                         cwd=str(HERE), creationflags=flags)
        self.window.destroy()

    # ---- uninstalling ------------------------------------------------------------------------

    def uninstall_plan(self):
        return uninstall.plan()

    def uninstall(self, keep_voices=True):
        """Start the removal (it waits for this window to close), then close the window."""
        if self.process and self.process.poll() is None:
            return False
        uninstall.start(keep_voices, os.getpid())
        self.window.destroy()
        return True

    def reveal(self, path):
        open_in_file_manager(path, select=True)

    def open_voices(self):
        VOICES_DIR.mkdir(exist_ok=True)
        open_in_file_manager(VOICES_DIR)
        return self.voices()

    def open_error_log(self):
        if ERROR_LOG.exists():
            open_in_file_manager(ERROR_LOG)


def main():
    api = Api()
    window = webview.create_window(
        "Subtitle Maker", str(HERE / "ui" / "index.html"), js_api=api,
        width=1040, height=720, min_size=(760, 560), background_color="#f3f1ec",
    )
    api.window = window
    # Videos dropped onto the "Subtitle Maker.bat" icon arrive as arguments
    opened_with = [str(Path(a).absolute()) for a in sys.argv[1:] if Path(a).is_file()]

    def on_loaded():
        # Dropped files only come with their full paths when Python handles the drop
        window.dom.document.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True)
        window.dom.document.events.drop += DOMEventHandler(api.on_drop, prevent_default=True, stop_propagation=True)
        if opened_with:
            window.evaluate_js(f"app.addFiles({json.dumps(opened_with)})")

    window.events.loaded += on_loaded
    window.events.closing += api.cancel
    if sys.platform == "win32":
        import ctypes

        # Its own taskbar button and icon, not grouped under Python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Veyra.SubtitleMaker")
    # Keep the web view's profile between launches: a fresh private one each time is slow to make
    webview.start(private_mode=False, storage_path=str(data_dir() / "webview"), icon=str(HERE / "ui" / "icon.ico"))


def report_crash(error):
    """There is no console behind the window, so say what went wrong in a message box and the log."""
    import traceback

    try:
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        ERROR_LOG.write_text("app.py\n\n" + "".join(traceback.format_exception(error)), encoding="utf-8")
    except OSError:
        pass
    message = (f"Subtitle Maker couldn't open its window.\n\n{error.__class__.__name__}: {error}\n\n"
               f"Try running Setup.bat again. Details are in {ERROR_LOG}")
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Subtitle Maker", 0x10)
    else:
        print(message, file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        if "--setup" in sys.argv:
            # Setup.bat falls back to asking its questions in the console, so no message box
            print(f"The setup window couldn't open ({e.__class__.__name__}: {e}).", file=sys.stderr)
        else:
            report_crash(e)
        sys.exit(1)
