"""The Subtitle Maker window: drop videos in, watch progress, name speakers, change settings.

It runs make_subtitles.py --events in the background for each file and shows what it reports,
so the window stays responsive, Cancel can stop a run cleanly, and the GPU memory is freed
after every video. Start it with "Subtitle Maker.bat" (or app.sh on Mac/Linux).
"""

import time

STARTED = time.perf_counter()  # for the startup timings in startup.log

import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from collections import deque  # noqa: E402
from pathlib import Path  # noqa: E402

import psutil  # noqa: E402
import webview  # noqa: E402
from webview.dom import DOMEventHandler  # noqa: E402

import configure  # noqa: E402
import models  # noqa: E402
import uninstall  # noqa: E402
import update  # noqa: E402
from paths import (DEFAULT_SETTINGS, ERROR_LOG, LEVELS, SETTINGS_FILE, data_dir, load_history,  # noqa: E402
                   load_settings, save_history, save_settings)

HERE = Path(__file__).parent
VOICES_DIR = HERE / "Voices"
EVENT_PREFIX = "@@"  # matches make_subtitles.EVENT_PREFIX
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".mov"}  # matches speakers.AUDIO_EXTS
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
STARTUP_LOG = data_dir() / "startup.log"


class Timings:
    """How long each stage of opening the window took, written to startup.log for comparing PCs."""

    def __init__(self):
        self.marks = []
        self.ready = threading.Event()

    def mark(self, stage):
        self.marks.append((stage, time.perf_counter() - STARTED))
        if stage == "window ready to use":
            self.ready.set()

    def save(self):
        try:
            STARTUP_LOG.parent.mkdir(parents=True, exist_ok=True)
            STARTUP_LOG.write_text("Seconds since Subtitle Maker started:\n" +
                                   "".join(f"{t:6.2f}  {stage}\n" for stage, t in self.marks), encoding="utf-8")
        except OSError:
            pass


TIMINGS = Timings()
READY_WITHIN = 40  # seconds before the watchdog says something
# --smoke-test (the Windows tests on GitHub): open, wait until ready, close; fail if it never gets ready
SMOKE_TEST = "--smoke-test" in sys.argv


def watchdog():
    """If the window still isn't ready after READY_WITHIN seconds, note it in startup.log and ask
    whether to keep waiting. This runs outside the window, so it works even if the window froze."""
    while not TIMINGS.ready.wait(READY_WITHIN):
        TIMINGS.mark(f"NOT READY after {READY_WITHIN} more seconds (last step above is where it got stuck)")
        TIMINGS.save()
        if SMOKE_TEST:
            os._exit(3)
        if sys.platform != "win32":
            continue
        import ctypes

        answer = ctypes.windll.user32.MessageBoxW(
            None, "Subtitle Maker is taking much longer than usual to open.\n\n"
                  "Retry keeps waiting. Cancel closes it; open it again, and if it keeps happening, "
                  f"send the file {STARTUP_LOG} along.",
            "Subtitle Maker", 0x5 | 0x30 | 0x40000)  # Retry/Cancel, warning icon, on top
        if answer == 2:  # Cancel
            os._exit(1)


def dark_mode():
    """Will the window be dark? Then it starts dark too, instead of flashing white."""
    theme = load_settings()["theme"]
    if theme != "system":
        return theme == "dark"
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


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
        # Everything that isn't for the page starts with _: pywebview walks every public attribute
        # (and theirs, recursively) on the window's main thread to build the page's API, and walking
        # into the window and its .NET form froze the window at startup ("Not Responding")
        self._window = None
        self._process = None  # a one-off run (downloading models)
        self._worker = None  # make_subtitles --serve: keeps the model loaded between videos
        self._job = None  # the worker's current job: {"cancelled", "reported"}
        self._lock = threading.Lock()
        # Look at the PC (nvidia-smi can take a second or two) while the window is still opening,
        # so the page never waits for it
        self._probe = {}
        self._probe_thread = threading.Thread(target=self.look_at_pc, daemon=True)
        self._probe_thread.start()

    def look_at_pc(self):
        gpu = configure.nvidia_gpu()
        ram_gb = psutil.virtual_memory().total / 2**30
        cores = psutil.cpu_count(logical=False) or os.cpu_count() or 2
        self._probe.update(
            recommended=configure.recommend(gpu, ram_gb, cores),
            hardware={"gpu": gpu[0] if gpu else None, "vram_mb": gpu[1] if gpu else None,
                      "ram_gb": round(ram_gb), "cores": cores},
            models=self.models_status(),
        )
        TIMINGS.mark("looked at this PC (graphics card, memory, models)")

    # ---- talking to the page ----------------------------------------------------------------

    def send(self, event):
        if self._window:
            self._window.evaluate_js(f"app.onEvent({json.dumps(event)})")
            if event["type"] == "progress" and event.get("total"):
                self._window.set_title(f"{event['done'] / event['total']:.0%} · Subtitle Maker")

    def on_drop(self, event):
        files = event.get("dataTransfer", {}).get("files", [])
        paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
        if paths:
            self._window.evaluate_js(f"app.addFiles({json.dumps(paths)})")

    # ---- state ------------------------------------------------------------------------------

    def init(self):
        TIMINGS.mark("page asked for its data")
        self._probe_thread.join()  # normally long finished by now
        return {
            "settings": load_settings(),
            "configured": SETTINGS_FILE.exists(),
            "levels": LEVELS,
            "recommended": self._probe["recommended"],
            "hardware": self._probe["hardware"],
            "voices": self.voices(),
            "platform": sys.platform,
            "version": update.installed(),
            "models": self._probe["models"],
            # Setup.bat opens the window with --setup; Update.bat with --update
            "mode": "setup" if "--setup" in sys.argv else "update" if "--update" in sys.argv else None,
        }

    def page_ready(self):
        """The page has drawn itself: the window is fully open."""
        TIMINGS.mark("window ready to use")
        TIMINGS.save()
        if SMOKE_TEST:
            threading.Timer(1.0, self._window.destroy).start()

    def restart_app(self):
        """Close this window and open a fresh one (the page offers this if starting goes wrong)."""
        self.shutdown()
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        subprocess.Popen([str(pythonw if pythonw.exists() else exe), str(HERE / "app.py")], cwd=str(HERE),
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        self._window.destroy()

    def open_startup_log(self):
        TIMINGS.save()
        open_in_file_manager(STARTUP_LOG)

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
        picked = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True, file_types=kinds)
        return list(picked or [])

    # ---- running make_subtitles.py ------------------------------------------------------------

    def start(self, path, speakers=None):
        """Make subtitles for one file. Progress arrives in the page as events, ending with "exit"."""
        argv = [str(Path(path).absolute()), "--events"]  # the worker runs in the Tools folder
        if speakers is not None:
            argv += ["--speakers", str(int(speakers))]
        with self._lock:
            if self._busy():
                return False
            self._job = {"cancelled": False, "reported": False}
            if not self._tell_worker({"type": "job", "argv": argv}):
                self._job = None
                return False
        return True

    def warm(self):
        """Start loading the speech model now (files were just added), so the first video starts sooner."""
        with self._lock:
            if not self._busy() and SETTINGS_FILE.exists():
                self._tell_worker({"type": "warm"})

    def _busy(self):
        return bool(self._job) or bool(self._process and self._process.poll() is None)

    def _tell_worker(self, request):
        """Send a request to the worker, starting one if none is running. Call with _lock held."""
        if not (self._worker and self._worker.poll() is None):
            self._worker = self._spawn(["--serve"])
            threading.Thread(target=self._follow_worker, args=(self._worker,), daemon=True).start()
        try:
            self._worker.stdin.write(json.dumps(request) + "\n")
            self._worker.stdin.flush()
            return True
        except OSError:
            return False

    def _spawn(self, args):
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
               "PYTHONIOENCODING": "utf-8"}
        return subprocess.Popen(
            [console_python(), str(HERE / "make_subtitles.py"), *args, "--events"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            cwd=str(HERE), env=env, creationflags=NO_WINDOW,
        )

    def _follow_worker(self, worker):
        """Pass the worker's events to the page; each job ends with an "exit" event, as a one-off run does."""
        errors = deque(maxlen=30)
        threading.Thread(target=lambda: errors.extend(worker.stderr), daemon=True).start()
        for line in worker.stdout:
            line = line.rstrip("\n")
            if not line.startswith(EVENT_PREFIX):
                if line.strip() and self._job:
                    self.send({"type": "log", "text": line})
                continue
            event = json.loads(line[len(EVENT_PREFIX):])
            kind = event["type"]
            if kind == "finished":
                self._job = None
                self._finish_title()
                self.send({"type": "exit", "code": event["code"], "cancelled": False})
            elif kind in ("warmed", "idle_exit", "model_loaded"):
                continue
            elif self._job:
                self._job["reported"] |= kind in ("done", "error")
                self._beep_if_done(event)
                self.send(event)
        code = worker.wait()
        job, self._job = self._job, None
        if job:  # the worker ended mid-job: cancelled, or it crashed
            if not job["cancelled"] and not job["reported"]:
                tail = "".join(errors).strip().splitlines()
                self.send({"type": "error", "message": tail[-1] if tail else f"It stopped unexpectedly (code {code}).",
                           "log": None})
            self._finish_title()
            self.send({"type": "exit", "code": code or 1, "cancelled": job["cancelled"]})

    def _beep_if_done(self, event):
        if event["type"] == "done" and sys.platform == "win32" and load_settings()["done_sound"]:
            import winsound

            winsound.MessageBeep()

    def _finish_title(self):
        if self._window:
            self._window.set_title("Subtitle Maker")

    def stop_worker(self):
        """Stop the worker (and free its memory): before closing, uninstalling or updating."""
        if self._worker and self._worker.poll() is None:
            if self._job:
                self._job["cancelled"] = True
            self._worker.kill()
            self._worker.wait(timeout=10)

    def prepare(self):
        """Download what the saved settings need (after Settings changes)."""
        return self.run(["--prepare"])

    def run(self, args):
        """A one-off run (downloading models)."""
        with self._lock:
            if self._busy():
                return False
            self._process = self._spawn(args)
        threading.Thread(target=self._follow, args=(self._process,), daemon=True).start()
        return True

    def _follow(self, process):
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
                self.send(event)
            elif line.strip():
                self.send({"type": "log", "text": line})
        code = process.wait()
        stderr_thread.join(timeout=2)
        if code and not reported and not getattr(process, "cancelled", False):
            tail = "".join(errors).strip().splitlines()
            self.send({"type": "error", "message": tail[-1] if tail else f"It stopped unexpectedly (code {code}).",
                       "log": None})
        self._finish_title()
        self.send({"type": "exit", "code": code, "cancelled": getattr(process, "cancelled", False)})

    def answer(self, name):
        """The name typed for the speaker the current video is asking about."""
        if self._job and self._worker and self._worker.poll() is None:
            self._worker.stdin.write(name.replace("\n", " ") + "\n")
            self._worker.stdin.flush()

    def review_clip(self, clip_id, start, end):
        """Play back a line while checking: the audio comes back as a "clip" event."""
        self._to_job({"type": "clip", "id": clip_id, "start": float(start), "end": float(end)})

    def review_done(self, cues, names):
        """The checked lines: the worker saves these."""
        self._to_job({"type": "review_done", "cues": cues, "names": names})

    def _to_job(self, request):
        if self._job and self._worker and self._worker.poll() is None:
            self._worker.stdin.write(json.dumps(request) + "\n")
            self._worker.stdin.flush()

    def cancel(self):
        if self._job:
            self.stop_worker()  # the next video starts a fresh worker
        elif self._process and self._process.poll() is None:
            self._process.cancelled = True
            self._process.kill()

    def shutdown(self):
        """The window is closing: stop everything still running."""
        self.cancel()
        self.stop_worker()

    # ---- settings and folders ---------------------------------------------------------------

    def save_settings(self, level, language):
        settings = load_settings()
        settings.update(level=level, language=language or None)
        save_settings(settings)
        return settings

    # ---- where subtitles go ------------------------------------------------------------------

    def choose_folder(self):
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return picked[0] if picked else None

    # Settings that take effect straight away (quality and language have their own Save, since
    # they can mean a download)
    _INSTANT = ("max_chars", "max_lines", "min_seconds", "max_seconds", "pause_split", "speaker_labels",
               "speaker_files", "hotwords", "remove_fillers", "vad", "device", "transcript", "review", "done_sound",
               "open_folder", "theme", "auto_update")

    def save_prefs(self, changes):
        """Save some instant settings; anything not allowed falls back to its default."""
        settings = load_settings()
        settings.update({k: v for k, v in changes.items() if k in self._INSTANT})
        save_settings(settings)
        return load_settings()  # the cleaned-up version

    def reset_prefs(self):
        settings = load_settings()
        settings.update({k: DEFAULT_SETTINGS[k] for k in self._INSTANT})
        save_settings(settings)
        return settings

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
                self._pending_packages = update.apply(latest, report, packages=False)
                self.send({"type": "update_done", "packages": bool(self._pending_packages)})
            except Exception as e:
                self.send({"type": "update_done", "error": f"The update didn't finish: {e}"})
        if self._busy():
            return False
        self.stop_worker()  # it runs the old files, and would keep them in use
        threading.Thread(target=work, daemon=True).start()
        return True

    def restart(self):
        """Close this window and open the new version (after installing any new packages)."""
        # A visible console only when there are packages to install, so the wait isn't a mystery
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if getattr(self, "_pending_packages", None) else NO_WINDOW
        self.stop_worker()
        subprocess.Popen([console_python(), str(HERE / "update.py"), "--finish", str(os.getpid())],
                         cwd=str(HERE), creationflags=flags)
        self._window.destroy()

    # ---- uninstalling ------------------------------------------------------------------------

    def uninstall_plan(self):
        return uninstall.plan()

    def uninstall(self, keep_voices=True):
        """Start the removal (it waits for this window to close), then close the window."""
        if self._busy():
            return False
        self.stop_worker()  # its Python would otherwise stay in use and couldn't be deleted
        uninstall.start(keep_voices, os.getpid())
        self._window.destroy()
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
    TIMINGS.mark("Python and the app's code loaded")
    threading.Thread(target=watchdog, daemon=True).start()
    api = Api()
    window = webview.create_window(
        "Subtitle Maker", str(HERE / "ui" / "index.html"), js_api=api,
        width=1040, height=720, min_size=(760, 560), background_color="#15140f" if dark_mode() else "#f3f1ec",
    )
    api._window = window
    # Videos dropped onto the "Subtitle Maker.bat" icon arrive as arguments
    opened_with = [str(Path(a).absolute()) for a in sys.argv[1:] if Path(a).is_file()]

    window.events.shown += lambda: TIMINGS.mark("window shown (web view started)")

    def on_loaded():
        TIMINGS.mark("page loaded")
        # Dropped files only come with their full paths when Python handles the drop
        window.dom.document.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True)
        window.dom.document.events.drop += DOMEventHandler(api.on_drop, prevent_default=True, stop_propagation=True)
        if opened_with:
            window.evaluate_js(f"app.addFiles({json.dumps(opened_with)})")

    window.events.loaded += on_loaded
    window.events.closing += api.shutdown
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
