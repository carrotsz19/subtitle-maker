"""The Subtitle Maker window: drop videos in, watch progress, name speakers, change settings.

It runs make_subtitles.py --events in the background for each file and shows what it reports,
so the window stays responsive, Cancel can stop a run cleanly, and the GPU memory is freed
after every video. Start it with "Subtitle Maker.bat" (or app.sh on Mac/Linux).
"""

import time

STARTED = time.perf_counter()  # for the startup timings in startup.log

import json  # noqa: E402
import os  # noqa: E402
import platform  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from collections import deque  # noqa: E402
from pathlib import Path  # noqa: E402

import psutil  # noqa: E402
import webview  # noqa: E402
from webview.dom import DOMEventHandler  # noqa: E402

import changelog  # noqa: E402
import configure  # noqa: E402
import hardware  # noqa: E402
import known_voices  # noqa: E402
import models  # noqa: E402
import srtfile  # noqa: E402
import uninstall  # noqa: E402
import update  # noqa: E402
import winapp  # noqa: E402
import winextras  # noqa: E402
from paths import dark_mode  # noqa: E402
from paths import (DEFAULT_SETTINGS, ERROR_LOG, LANGUAGES, LEVELS, SETTINGS_FILE, data_dir, load_history,  # noqa: E402
                   load_settings, save_history, save_settings)

HERE = Path(__file__).parent
VOICES_DIR = HERE / "Voices"
EVENT_PREFIX = "@@"  # matches make_subtitles.EVENT_PREFIX
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".mov"}  # matches speakers.AUDIO_EXTS
# What a dropped or chosen folder is searched for
MEDIA_EXTS = AUDIO_EXTS | {".mkv", ".avi", ".m4v", ".webm", ".wmv", ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp", ".opus", ".wma"}


def media_in(paths):
    """The paths, with each folder replaced by the videos and audio inside it (and its subfolders)."""
    found = []
    for p in map(Path, paths):
        if p.is_dir():
            found += sorted(str(f) for f in p.rglob("*") if f.is_file() and f.suffix.lower() in MEDIA_EXTS)
        elif p.is_file():
            found.append(str(p))
    return found
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
CREATE_SUSPENDED = 0x4
STARTUP_LOG = data_dir() / "startup.log"
QUEUE_FILE = data_dir() / "queue.json"  # what's still waiting, to offer to carry on next time


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


def kill_tree(process):
    """Stop a process and the ones it started (the worker runs speaker detection in its own)."""
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        children = []
    process.kill()
    for child in children:
        try:
            child.kill()
        except psutil.Error:
            pass


def log_startup(note):
    """Note something that went wrong with the window in startup.log (sent along with problems)."""
    TIMINGS.mark(note)
    TIMINGS.save()
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


class PageMail:
    """Delivers events to the page on a thread of its own, so nothing else ever waits for the window.

    pywebview's evaluate_js (and set_title) wait for the window's thread with no time limit, and
    sometimes never come back. When the thread reading the worker's output was the one waiting,
    the worker's output pipe filled up and it stopped dead mid-video: transcription and speaker
    detection both froze, with the graphics card idle. Now the reader only drops events here.
    A newer progress or resources event replaces one still waiting (only the latest matters),
    and a delivery the window doesn't answer within WAIT seconds is given up on, so one stuck
    call doesn't stop the page getting the rest.
    """

    WAIT = 10
    LATEST_ONLY = ("progress", "resources", "update_progress")
    MAX_STUCK = 3  # deliveries left waiting before the next one waits for one of them instead

    def __init__(self, api):
        self._api = api
        self._waiting = deque()
        self._ready = threading.Condition()
        self._stuck = []
        self.given_up = 0  # deliveries the window never answered (tests and the log read this)
        threading.Thread(target=self._run, daemon=True, name="page mail").start()

    def post(self, event):
        with self._ready:
            if event["type"] in self.LATEST_ONLY:
                for i, waiting in enumerate(self._waiting):
                    if waiting["type"] == event["type"]:
                        self._waiting[i] = event
                        return
            self._waiting.append(event)
            self._ready.notify()

    def flush(self, timeout=5.0):
        """Wait until everything posted so far has been delivered (tests)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._ready:
                if not self._waiting and not self._busy:
                    return True
            time.sleep(0.01)
        return False

    _busy = False

    def _run(self):
        while True:
            with self._ready:
                while not self._waiting:
                    self._ready.wait()
                event = self._waiting.popleft()
                self._busy = True
            try:
                self._send(event)
            finally:
                self._busy = False

    def _send(self, event):
        self._stuck = [d for d in self._stuck if not d.is_set()]
        while len(self._stuck) >= self.MAX_STUCK:  # the window isn't answering at all: wait for it
            self._stuck[0].wait(self.WAIT)
            self._stuck = [d for d in self._stuck if not d.is_set()]
        delivered = threading.Event()

        def deliver():
            try:
                self._api._deliver(event)
            except Exception as e:  # the window is closing, or the page threw: the next event still goes
                log_startup(f"Couldn't pass an event to the page: {e.__class__.__name__}: {e}")
            finally:
                delivered.set()

        threading.Thread(target=deliver, daemon=True).start()
        if not delivered.wait(self.WAIT):
            self.given_up += 1
            self._stuck.append(delivered)
            log_startup(f"The window didn't take a '{event['type']}' event within {self.WAIT} s; carrying on")


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
        self._taskbar = None  # progress on the taskbar button (Windows)
        self._edit = None  # the .srt open for fixing: {"path", "video"}
        self._closing_ok = False  # the app is closing itself (restart, update, uninstall): don't ask
        self._updating = False  # an update is being copied in: closing now would stop it partway
        self._outbox = PageMail(self)  # everything for the page goes through here (see PageMail)
        self._limit_note = None  # a Hardware limit Windows refused, said once a video starts
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
                      "ram_gb": round(ram_gb), "cores": cores, "threads": os.cpu_count() or cores,
                      # Settings > Hardware offers "Use less graphics memory" only where it changes something
                      "gpu_low_memory": configure.gpu_saves_memory() if gpu else False},
            models=self.models_status(),
        )
        TIMINGS.mark("looked at this PC (graphics card, memory, models)")

    # ---- talking to the page ----------------------------------------------------------------

    def send(self, event):
        """Pass an event to the page. Never waits for the window (see PageMail)."""
        self._outbox.post(event)

    def _show(self, script):
        """Run a call in the page, without waiting for the window."""
        self._outbox.post({"type": "_script", "script": script})

    def _deliver(self, event):
        """Called by PageMail, on its own thread: this one may wait for the window."""
        if not self._window:
            return
        if event["type"] == "_script":
            self._window.evaluate_js(event["script"])
        else:
            self._window.evaluate_js(f"app.onEvent({json.dumps(event)})")
            if event["type"] == "progress" and event.get("total"):
                # The whole video's progress (transcribing is only most of it); never 100% until saved
                fraction = min(event.get("overall", event["done"] / event["total"]), 0.99)
                self._window.set_title(f"{int(fraction * 100)}% · Subtitle Maker")
                self._progress(fraction)
            elif event["type"] == "progress":
                self._progress(state=winextras.INDETERMINATE)
            elif event["type"] == "error":
                self._progress(state=winextras.ERROR)
            elif event["type"] == "download":
                # copying a video from Google Drive, or downloading a model: on the taskbar too
                total = event.get("total")
                self._progress(min(event.get("done", 0) / total, 1.0) if total else None,
                               None if total else winextras.INDETERMINATE)

    def _progress(self, fraction=None, state=None):
        if self._window and winextras.WINDOWS:
            self._taskbar = self._taskbar or winextras.Taskbar(self._window)
            self._taskbar.set(fraction, state)

    def on_drop(self, event):
        files = event.get("dataTransfer", {}).get("files", [])
        self._take([f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")])

    def _take(self, paths):
        """Dropped or opened-with files: an .srt opens for fixing; videos join the queue."""
        srts = [p for p in paths if Path(p).suffix.lower() == ".srt" and Path(p).is_file()]
        media = media_in(p for p in paths if p not in srts)
        if media:
            self._show(f"app.addFiles({json.dumps(media)})")
        elif srts:
            self._show(f"app.openSrt({json.dumps(srts[0])})")

    # ---- state ------------------------------------------------------------------------------

    def init(self):
        TIMINGS.mark("page asked for its data")
        self._probe_thread.join()  # normally long finished by now
        return {
            "settings": load_settings(),
            "configured": SETTINGS_FILE.exists(),
            "levels": LEVELS,
            "languages": LANGUAGES,
            "recommended": self._probe["recommended"],
            "hardware": self._probe["hardware"],
            "voices": self.voices(),
            "platform": sys.platform,
            # The page draws the title bar, or Windows does; and whether the window is maximized and in front
            "frame": {"custom": FRAME["custom"], **(winextras.window_state(WINDOW_HANDLE[0]) if FRAME["custom"] else {})},
            "version": update.installed(),
            "release": changelog.current(),  # "1.0.0", from CHANGELOG.md
            "folder": str(HERE.resolve()),  # where the program is, for Settings > About
            # What's new since the version last opened: shown once, after an update
            "news": changelog.news(first_run=not SETTINGS_FILE.exists()),
            "models": self._probe["models"],
            # Setup.bat opens the window with --setup; Update.bat with --update
            # Settings > Apps > Uninstall opens it with --uninstall
            "mode": next((m for m in ("setup", "update", "uninstall") if f"--{m}" in sys.argv), None),
            "unfinished": self._unfinished(),
            "required": update.required(),  # an important security update must go in first
            # Settings > Hardware: its presets, and what the memory estimates add up from
            "hw": {"presets": hardware.PRESETS, "speakers_mb": hardware.SPEAKERS_RAM_MB,
                   "audio_mb": hardware.AUDIO_RAM_MB_PER_MINUTE * hardware.ASSUMED_MINUTES,
                   "minutes": hardware.ASSUMED_MINUTES, "default_threads": hardware.DEFAULT_THREADS},
        }

    def memory_check(self, speakers=False):
        """Before a queue starts: will a video at this quality fit in the memory limit?"""
        self._probe_thread.join()
        return hardware.memory_check(load_settings(), self._probe["hardware"], bool(speakers))

    # ---- what's new ------------------------------------------------------------------------

    def all_news(self):
        """Every version's changes, newest first (Settings > Software update > What's new)."""
        return changelog.versions()

    def news_seen(self):
        """The What's new sheet was shown: don't show it again until the next version."""
        changelog.mark_seen()
        return True

    # ---- carrying on with a queue that didn't finish ------------------------------------------

    def save_queue(self, paths):
        """The page keeps this up to date: the videos still waiting (or being worked on)."""
        try:
            if paths:
                QUEUE_FILE.write_text(json.dumps([str(p) for p in paths], indent=1), encoding="utf-8")
            else:
                QUEUE_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    def _unfinished(self):
        try:
            paths = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [p for p in paths if isinstance(p, str) and Path(p).is_file()]

    def queue_finished(self, done, failed):
        """The whole queue is done: say so if you're in another window (Settings > Notify me)."""
        self._progress(state=winextras.ERROR if failed else winextras.NO_PROGRESS)
        if not winextras.WINDOWS or not load_settings()["notify"] or not winextras.in_background(self._window):
            return False
        if failed:
            title = f"{failed} of {done + failed} videos didn't work" if done + failed > 1 else "The video didn't work"
        else:
            title = f"Subtitles ready for {done} videos" if done > 1 else "Subtitles ready"
        try:
            winextras.flash(self._window)
        except Exception:
            pass
        winextras.notify(self._window, HERE / "ui" / "icon.ico", title, "Click to open Subtitle Maker.",
                         on_click=lambda: winextras.bring_to_front(self._window))
        return True

    def checks_ready(self, count):
        """Every video in the queue is transcribed and the first check is open. Nothing more happens
        until someone's back, so: the done sound, and a notification if you're in another window."""
        settings = load_settings()
        if sys.platform == "win32" and settings["done_sound"]:
            import winsound

            winsound.MessageBeep()
        if not winextras.WINDOWS or not settings["notify"] or not winextras.in_background(self._window):
            return False
        title = f"{count} videos are ready to check" if count > 1 else "Subtitles are ready to check"
        try:
            winextras.flash(self._window)
        except Exception:
            pass
        winextras.notify(self._window, HERE / "ui" / "icon.ico", title, "Click to open Subtitle Maker.",
                         on_click=lambda: winextras.bring_to_front(self._window))
        return True

    # ---- fixing an existing .srt ---------------------------------------------------------------

    def choose_srt(self):
        picked = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("Subtitles (*.srt)", "All files (*.*)"))
        return picked[0] if picked else None

    def open_srt(self, path):
        """The lines of an .srt, to fix in the same screen as new ones."""
        if self._busy():
            return {"error": "Wait for the current video to finish first."}
        found = {}
        try:
            cues = srtfile.read_srt(path, found)
        except srtfile.SrtError as e:
            return {"error": str(e)}
        except OSError as e:
            return {"error": f"Couldn't open {Path(path).name}: {e.strerror or e}"}
        video = srtfile.video_for(path, MEDIA_EXTS)
        self._edit = {"path": str(Path(path).absolute()), "video": str(video) if video else None}
        threading.Thread(target=self._load_writer, daemon=True).start()  # ready by the time you save
        settings = load_settings()
        return {"path": self._edit["path"], "file": Path(path).name, "video": video.name if video else None,
                "cues": cues, "max_chars": settings["max_chars"], "max_lines": settings["max_lines"],
                "formatted": bool(found.get("formatted"))}

    def _load_writer(self):
        try:
            # A second or two the first time (it loads Whisper's libraries), so do it before Save
            __import__("make_subtitles")
        except Exception:
            pass

    def edit_clip(self, start, end):
        """That line of the video the open .srt belongs to, as a playable data: URL (or None)."""
        if not self._edit or not self._edit["video"]:
            return None
        import base64

        try:
            data = srtfile.clip_wav(self._edit["video"], float(start), float(end))
        except Exception:
            return None
        return "data:audio/wav;base64," + base64.b64encode(data).decode("ascii") if data else None

    def save_srt(self, cues):
        """Save the fixed lines over the .srt (the old one goes to the backups folder first)."""
        if not self._edit:
            return {"error": "Nothing is open."}
        path = Path(self._edit["path"])
        lines = sorted(([float(c["start"]), float(c["end"]), " ".join(str(c["text"]).split()), None]
                        for c in cues if str(c["text"]).strip()), key=lambda c: c[0])
        if not lines:
            return {"error": "Every line was deleted, so there's nothing to save."}
        import make_subtitles as ms

        ms.apply_style(load_settings())
        try:
            backup = srtfile.back_up(path) if path.exists() else None
            ms.write_srt(path, lines, None, "none")
            vtt = path.with_suffix(".vtt")
            if vtt.exists():  # keep a .vtt made alongside it the same
                ms.write_vtt(vtt, lines, None, "none")
        except OSError as e:
            return {"error": f"Couldn't save {path.name}: {e.strerror or e}. Is it open in another program?"}
        self._edit = None
        return {"path": str(path), "count": len(lines), "backup": str(backup) if backup else None}

    def close_srt(self):
        self._edit = None

    def open_backups(self):
        srtfile.BACKUPS.mkdir(parents=True, exist_ok=True)
        open_in_file_manager(srtfile.BACKUPS)

    # ---- remembered voices -----------------------------------------------------------------------

    def known_voices(self):
        return known_voices.listing()

    def forget_voice(self, name):
        return known_voices.forget(name)

    def forget_all_voices(self):
        return known_voices.forget_all()

    # ---- word fixes ----------------------------------------------------------------------------

    def set_fixes(self, fixes):
        settings = load_settings()
        settings["fixes"] = fixes
        save_settings(settings)
        return load_settings()

    def add_fix(self, heard, meant):
        settings = load_settings()
        return self.set_fixes(settings["fixes"] + [[heard, meant]])

    def page_ready(self):
        """The page has drawn itself: the window is fully open."""
        TIMINGS.mark("window ready to use")
        TIMINGS.save()
        # A Start menu entry and Settings > Apps (only does anything the first time, or after moving or updating)
        threading.Thread(target=lambda: (TIMINGS.mark(winapp.ensure()), TIMINGS.save()), daemon=True).start()
        if SMOKE_TEST:
            threading.Thread(target=self._smoke_test_extras, daemon=True).start()

    def _smoke_test_extras(self):
        """The Windows-only touches, tried for real in the Windows tests, then close."""
        if winextras.WINDOWS:
            import time

            shown = threading.Event()
            result = {}
            self._progress(0.5)
            try:
                winextras.in_background(self._window)
                winextras.flash(self._window)
                TIMINGS.mark("window handle and flashing: ok")
            except Exception as e:
                TIMINGS.mark(f"window handle and flashing: {e.__class__.__name__}: {e}")
            winextras.notify(self._window, HERE / "ui" / "icon.ico", "Subtitle Maker test", "Checking notifications work.",
                             done=lambda error: (result.update(error=error), shown.set()))
            shown.wait(10)
            time.sleep(1)  # the taskbar call runs on the window's thread
            self._progress(state=winextras.NO_PROGRESS)
            time.sleep(0.5)
            shell = any((proc.info["name"] or "").lower() == "explorer.exe" for proc in psutil.process_iter(["name"]))
            TIMINGS.mark(f"taskbar progress: {self._taskbar.error or ('ok' if self._taskbar.ok else 'nothing happened')}"
                         f" (Windows shell {'running' if shell else 'not running'})")
            TIMINGS.mark(f"notification: {result.get('error') or 'ok' if shown.is_set() else 'never shown'}")
            TIMINGS.save()
        self._closing_ok = True
        self._window.destroy()

    def restart_app(self):
        """Close this window and open a fresh one (the page offers this if starting goes wrong)."""
        self._closing_ok = True
        self.shutdown()
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        subprocess.Popen([str(pythonw if pythonw.exists() else exe), str(HERE / "app.py")], cwd=str(HERE),
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        self._window.destroy()

    # ---- the page's title bar ---------------------------------------------------------------

    def window_button(self, name):
        """Minimise, maximise (or restore) and close, as Windows' own buttons do them: closing
        asks first while a video is running, and saves where the window was (_on_closing)."""
        if FRAME["custom"] and name in ("minimise", "maximise", "close"):
            winextras.window_command(WINDOW_HANDLE[0], name)

    def window_resize(self, edge, x=None, y=None, double=False):
        """The pointer went down on the page's top edge, at (x, y) on the screen in pixels: resize
        from it, as from Windows' own border (see winextras.begin_resize)."""
        if not (FRAME["custom"] and edge in winextras.EDGES):
            return
        hwnd, form = WINDOW_HANDLE[0], self._window.native
        at = (x, y) if isinstance(x, (int, float)) and isinstance(y, (int, float)) else None

        def resize():
            least = form.MinimumSize  # read on the window's thread
            winextras.begin_resize(hwnd, edge, at, bool(double), (least.Width, least.Height))

        winextras.ui_thread(self._window, resize)

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

    def choose_video_folder(self):
        """Every video and audio file in a folder (and its subfolders)."""
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return media_in(picked) if picked else []

    def queue_info(self, paths):
        """For each queued file: does it already have subtitles where the new ones would go?"""
        out_dir = load_settings()["output_dir"]
        info = {}
        for p in paths:
            video = Path(p)
            srt = (Path(out_dir) / f"{video.stem}.srt") if out_dir and Path(out_dir).is_dir() else video.with_suffix(".srt")
            info[p] = {"has_srt": srt.exists(), "exists": video.is_file()}
        return info

    # ---- downloaded models ------------------------------------------------------------------

    def models_list(self):
        current = LEVELS[load_settings()["level"]]["model"]
        return [dict(m, current=m["model"] == current) for m in models.downloaded()]

    def delete_model(self, model):
        """Free the space a downloaded speech model takes (not the one the current level uses)."""
        # Only a model Setup offers: the name becomes a folder to delete, so nothing else may get through
        if model not in {lv["model"] for lv in LEVELS.values()}:
            return False
        if model == LEVELS[load_settings()["level"]]["model"] or self._busy():
            return False
        self.stop_worker()  # it may have this one loaded
        import shutil

        shutil.rmtree(models.cache_folder(model), ignore_errors=True)
        return self.models_list()
    # ---- running make_subtitles.py ------------------------------------------------------------

    def start(self, path, speakers=None, later=False):
        """Make subtitles for one file. Progress arrives in the page as events, ending with "exit".

        later: transcribe it now and check it once the queue is done (a "pending" event says
        where its lines were put; finish() checks and saves them)."""
        argv = [str(Path(path).absolute()), "--events"]  # the worker runs in the Tools folder
        if speakers is not None:
            argv += ["--speakers", str(int(speakers))]
        if later:
            argv.append("--later")
        return self._run_job(argv)

    def finish(self, path, bundle):
        """Check and save a video's lines that were set aside (its pending .json)."""
        return self._run_job([str(Path(path).absolute()), "--events", "--finish", str(bundle)], checking=True)

    def pending_checks(self):
        """Videos transcribed but not checked yet (the window was closed first), oldest first."""
        found = []
        for bundle in sorted((data_dir() / "pending").glob("*.json")):
            try:
                saved = json.loads(bundle.read_text(encoding="utf-8"))
                found.append({"bundle": str(bundle), "path": saved["input"], "file": Path(saved["out"]).name})
            except (OSError, ValueError, KeyError):
                continue
        return found

    def drop_check(self, bundle):
        """Forget a video's lines that were waiting to be checked."""
        bundle = Path(bundle)
        pending = data_dir() / "pending"
        if bundle.parent.resolve() != pending.resolve() or bundle.suffix != ".json":
            return False  # only ever its own files
        for f in (bundle, bundle.with_suffix(".npy")):
            f.unlink(missing_ok=True)
        return True

    def review_discard(self):
        """Discard the lines being checked: nothing for this video is saved."""
        self._to_job({"type": "review_discard"})

    def _run_job(self, argv, checking=False):
        with self._lock:
            if self._busy():
                return False
            self._limits_changed()  # before the job is set, so stopping the old worker can't touch it
            if self._worker and self._worker.poll() is None:
                self._memory_hit(self._worker, fresh=True)  # only this video's going over counts
            self._job = {"cancelled": False, "reported": False, "checking": checking}
            if not self._tell_worker({"type": "job", "argv": argv}):
                self._job = None
                return False
            if self._limit_note:  # from a worker started before this video (warming up): say it now
                self.send({"type": "log", "text": self._limit_note})
                self._limit_note = None
        return True

    def warm(self):
        """Start loading the speech model now (files were just added), so the first video starts sooner."""
        with self._lock:
            if not self._busy() and SETTINGS_FILE.exists():
                self._limits_changed()
                self._tell_worker({"type": "warm"})

    def _limits_changed(self):
        """Settings > Hardware changed since the worker started (or it went over its memory limit):
        stop it, so the next video starts a fresh one with the new limits. Call with _lock held,
        between videos."""
        worker = self._worker
        if worker and worker.poll() is None and (
                worker.limits != hardware.limits(load_settings()) or getattr(worker, "restart", False)):
            self.stop_worker()
            # Forgotten straight away: its thread, seeing it end, then knows the next job isn't its own
            self._worker = None

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
        """Start make_subtitles.py under the limits in Settings > Hardware (see hardware.py): its
        threads by environment, then its priority and a Job Object, which what it starts (speaker
        detection) inherits."""
        settings = load_settings()
        limits = hardware.limits(settings)
        env = {**hardware.worker_env(settings), "PYTHONUNBUFFERED": "1", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
               "PYTHONIOENCODING": "utf-8"}
        # The venv's python.exe is a launcher that starts the real Python as its child straight away,
        # so the priority is given at the start (children inherit it), and with a Job Object the
        # launcher starts suspended until it's in the job, so the real Python can't start outside it
        flags = NO_WINDOW
        jobbed = winextras.WINDOWS and bool(limits["cpu_percent"] or limits["memory_mb"])
        if jobbed:
            env[hardware.ENV_IN_JOB] = "1"
        if winextras.WINDOWS and limits["low_priority"]:
            flags |= BELOW_NORMAL
        if jobbed:
            flags |= CREATE_SUSPENDED
        process = subprocess.Popen(
            [console_python(), str(HERE / "make_subtitles.py"), *args, "--events"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            cwd=str(HERE), env=env, creationflags=flags,
        )
        process.limits, process.job = limits, None
        try:
            # (a limit Windows refuses is left out and said below: the video still runs)
            process.job, problem = winextras.limit_job(process, limits["cpu_percent"], limits["memory_mb"])
            if jobbed:
                psutil.Process(process.pid).resume()  # in the job (or not, if Windows refused): off it goes
        except Exception:
            # Never leave a suspended process behind
            kill_tree(process)
            if process.job:
                process.job.close()
            raise
        if not winextras.WINDOWS:
            problem = winextras.lower_priority(process, limits["low_priority"]) or problem
        if problem:  # it still runs, only without that limit: say so in Details and startup.log
            TIMINGS.mark(f"hardware limits: {problem}")
            note = f"Couldn't apply every limit in Settings > Hardware ({problem})."
            if self._job:
                self.send({"type": "log", "text": note})
            else:
                self._limit_note = note  # the window only shows Details while a video runs
        return process

    def _memory_hit(self, process, fresh=False):
        """Did Windows refuse this process's job memory for going over the limit, during the
        current video? fresh: a video is starting, so forget earlier ones."""
        job = getattr(process, "job", None)
        new = bool(job and job.handle and job.new_hits())
        process.mem_hit = False if fresh else getattr(process, "mem_hit", False) or new  # (fresh: earlier hits drop)
        return process.mem_hit

    def _plain_error(self, process, message, code=None):
        """The error to show, and whether it was memory running out (see hardware.memory_message)."""
        hit = self._memory_hit(process)
        plain = hardware.memory_message(message, process.limits["memory_mb"], hit=hit, code=code)
        memory = plain != message or hit
        if memory:
            process.restart = True  # the next video starts a fresh worker
        return plain, memory

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
                if worker is not self._worker:
                    continue
                self._job = None
                self._finish_title()
                self.send({"type": "exit", "code": event["code"], "cancelled": False})
            elif kind in ("warmed", "idle_exit", "model_loaded"):
                continue
            elif self._job and worker is self._worker:
                self._job["reported"] |= kind in ("done", "error")
                self._beep_if_done(event)
                if kind == "error" and event.get("memory"):
                    worker.restart = True
                elif kind == "error" and not event.get("friendly"):  # (a message meant for the user stays)
                    event["message"], memory = self._plain_error(worker, event["message"])
                    if memory:
                        event.update(memory=True, log=None)
                self.send(event)
        code = worker.wait()
        try:
            self._worker_ended(worker, code, errors)
        finally:
            if worker.job:
                worker.job.close()

    def _worker_ended(self, worker, code, errors):
        if worker is not self._worker:
            return  # an older worker, stopped between videos: the job running now isn't its
        job, self._job = self._job, None
        if job:  # the worker ended mid-job: cancelled, or it crashed
            if not job["cancelled"] and not job["reported"]:
                tail = "".join(errors).strip().splitlines()
                message, memory = self._plain_error(
                    worker, tail[-1] if tail else f"It stopped unexpectedly (code {code}).", code)
                self.send({"type": "error", "message": message, "log": None, **({"memory": True} if memory else {})})
            self._finish_title()
            self.send({"type": "exit", "code": code or 1, "cancelled": job["cancelled"]})

    def _beep_if_done(self, event):
        if event["type"] == "done" and sys.platform == "win32" and load_settings()["done_sound"]:
            import winsound

            winsound.MessageBeep()

    def _finish_title(self):
        if self._window:
            self._window.set_title("Subtitle Maker")
            self._progress(state=winextras.NO_PROGRESS)  # the next video, or the end of the queue, sets it again

    def stop_worker(self):
        """Stop the worker (and free its memory): before closing, uninstalling or updating."""
        if self._worker and self._worker.poll() is None:
            if self._job:
                self._job["cancelled"] = True
            kill_tree(self._worker)
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
            message, _ = self._plain_error(process, tail[-1] if tail else f"It stopped unexpectedly (code {code}).", code)
            self.send({"type": "error", "message": message, "log": None})
        if process.job:
            process.job.close()
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

    def _on_closing(self):
        """The window's X (or Alt+F4): ask first if that would stop work in progress. False keeps it open.

        Runs on the window's own thread, so it asks with a Windows message box: the page can't
        be asked from here without waiting on this same thread.
        """
        if not self._closing_ok and (self._busy() or self._updating) and winextras.WINDOWS:
            import ctypes

            if self._updating:
                text = "An update is being installed. Closing now could leave it half done, so it's best to wait a moment."
            elif self._job and self._job.get("checking"):
                text = ("You're checking a video's lines. Closing keeps them as they were transcribed, and "
                        "they open to check again next time (changes made in this check aren't kept).")
            elif self._job:
                text = ("A video is still being worked on. Closing stops it, and that video starts again "
                        "from the beginning next time (the rest of the queue is kept).")
            else:
                text = "Models are still downloading. Closing stops the download; it carries on from where it got to next time."
            try:
                owner = winextras.hwnd_of(self._window)
            except Exception:
                owner = None
            answer = ctypes.windll.user32.MessageBoxW(owner, text + "\n\nClose Subtitle Maker anyway?", "Subtitle Maker",
                                                      0x4 | 0x30 | 0x100)  # Yes/No, warning icon, No by default
            if answer != 6:  # Yes
                return False
        self.shutdown()
        return True

    def shutdown(self):
        """The window is closing: stop everything still running, and remember where the window was."""
        self.cancel()
        self.stop_worker()
        # Windows' record of the window (size, place, maximized, which screen); elsewhere, its size and place
        placement = winextras.get_placement(WINDOW_HANDLE[0]) if WINDOW_HANDLE else None
        if placement or GEOMETRY:
            settings = load_settings()
            if placement:
                settings["placement"] = placement
            if GEOMETRY:
                settings["window"] = dict(GEOMETRY)
            save_settings(settings)

    # ---- settings and folders ---------------------------------------------------------------

    def save_settings(self, level, language):
        settings = load_settings()
        settings.update(level=level, language=language if language in LANGUAGES else None)
        save_settings(settings)
        return load_settings()

    # ---- where subtitles go ------------------------------------------------------------------

    def choose_folder(self):
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return picked[0] if picked else None

    # Settings that take effect straight away (quality and language have their own Save, since
    # they can mean a download)
    _INSTANT = ("max_chars", "max_lines", "min_seconds", "max_seconds", "pause_split", "speaker_labels",
               "speaker_files", "hotwords", "remove_fillers", "vad", "device", "transcript", "review", "done_sound",
               "open_folder", "theme", "auto_update", "vtt", "multi", "people", "notify", "remember_voices", "less_motion",
               "translate", "remember_scroll", *hardware.KEYS)

    def save_prefs(self, changes):
        """Save some instant settings; anything not allowed falls back to its default.
        {"hw_preset": "light"} sets everything that preset does (Settings > Hardware)."""
        settings = load_settings()
        settings.update(hardware.PRESETS.get(changes.get("hw_preset"), {}))
        settings.update({k: v for k, v in changes.items() if k in self._INSTANT})
        save_settings(settings)
        # A worker waiting between videos with other limits (or still holding the graphics card
        # after switching to the processor) is stopped now; the next video starts a fresh one
        if getattr(self, "_window", None) and self._lock.acquire(blocking=False):
            try:
                if not self._busy():
                    self._limits_changed()
            finally:
                self._lock.release()
        return load_settings()  # the cleaned-up version

    def reset_prefs(self):
        settings = load_settings()
        settings.update({k: DEFAULT_SETTINGS[k] for k in self._INSTANT})
        save_settings(settings)
        return settings

    def set_channel(self, branch):
        """Follow the stable or the experimental version (the page then offers the switch)."""
        settings = load_settings()
        settings["channel"] = branch if branch in update.CHANNELS else "main"
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
            finally:
                self._updating = False
        if self._busy() or self._updating:
            return False
        self._updating = True
        self.stop_worker()  # it runs the old files, and would keep them in use
        threading.Thread(target=work, daemon=True).start()
        return True

    def restart(self):
        """Close this window and open the new version (after installing any new packages)."""
        # No console: new packages install behind a small window of their own (finishing.py)
        self._closing_ok = True
        self.stop_worker()
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        subprocess.Popen([str(pythonw if pythonw.exists() else console_python()), str(HERE / "update.py"), "--finish", str(os.getpid())],
                         cwd=str(HERE), creationflags=NO_WINDOW)
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
        self._closing_ok = True
        self._window.destroy()
        return True

    def reveal(self, path):
        open_in_file_manager(path, select=True)

    def open_program_folder(self):
        """Settings > About > Open folder: the folder the program is in."""
        open_in_file_manager(HERE)

    def open_voices(self):
        VOICES_DIR.mkdir(exist_ok=True)
        open_in_file_manager(VOICES_DIR)
        return self.voices()

    def error_details(self, path, message):
        """What to paste when asking for help with a failed video: the error, this PC and the log."""
        settings, version = load_settings(), update.installed()
        hw = self._probe["hardware"]
        limits = hardware.limits(settings)
        lines = [f"Subtitle Maker {changelog.current() or '(version unknown)'}"
                 f" ({version['sha'][:7] if version else 'build unknown'}) on {platform.platform()}",
                 f"Video: {Path(path).name}", f"Error: {message}",
                 f"Quality: {settings['level']}, run on: {settings['device']}, language: {settings['language'] or 'auto'}",
                 f"Hardware limits: processor {limits['cpu_percent'] or 'no limit'}{'%' if limits['cpu_percent'] else ''}, "
                 f"memory {str(limits['memory_mb']) + ' MB' if limits['memory_mb'] else 'no limit'}, "
                 f"{'below normal' if limits['low_priority'] else 'normal'} priority, "
                 f"less graphics memory {'on' if limits['gpu_low_memory'] else 'off'}",
                 f"PC: {hw.get('gpu') or 'no NVIDIA GPU'}, {hw.get('ram_gb')} GB RAM, {hw.get('cores')} cores"
                 f" ({hw.get('threads') or '?'} threads)"]
        try:
            log = ERROR_LOG.read_text(encoding="utf-8", errors="replace")
            lines += ["", "Last error log:", log[-4000:]]
        except OSError:
            pass
        return "\n".join(lines)

    def open_error_log(self):
        if ERROR_LOG.exists():
            open_in_file_manager(ERROR_LOG)


GEOMETRY = {}  # the window's size and place as it's moved and resized, saved when it closes
WINDOW_HANDLE = []  # the window's handle on Windows, once it exists
FRAME = {"custom": False}  # True once the page draws the title bar (see winextras.custom_frame)
TITLE_BAR = 40  # the page's drag strip, in its pixels (the same as --titlebar in ui/index.html)
CAPTION_BUTTONS = 3 * 46  # minimise, maximise and close, at its right


def own_title_bar(window, api):
    """Before the window first shows (on its own thread): take Windows' title bar off, so the
    page's own is the one there. If that fails (or SUBTITLE_MAKER_NATIVE_FRAME is set) the window
    keeps Windows' title bar, and the page leaves its own buttons out."""
    if not winextras.WINDOWS:
        return
    hwnd = WINDOW_HANDLE[0]
    if os.environ.get("SUBTITLE_MAKER_NATIVE_FRAME"):
        TIMINGS.mark("title bar: Windows' own (SUBTITLE_MAKER_NATIVE_FRAME)")
        return
    why = winextras.custom_frame(hwnd, on_state=lambda state: api.send({"type": "window", **state}))
    if why:
        TIMINGS.mark(f"title bar: Windows' own, since the page's couldn't be used ({why})")
        return
    FRAME["custom"] = True
    TIMINGS.mark("title bar: the page's own")

    def without_strip(e):
        # No strip to move the window by: Windows' title bar comes back. This normally happens
        # before the page asks api().init() whose title bar it is; if the page is up already, it's
        # told, and takes its own away.
        FRAME["custom"] = False
        winextras.native_frame(hwnd)
        api.send({"type": "window", "custom": False})
        TIMINGS.mark(f"title bar: Windows' own, since the drag strip couldn't be turned on ({e.__class__.__name__}: {e})")

    def turn_on(core):
        try:
            drag_strip(core)
            TIMINGS.mark("title bar: drag strip on")
        except Exception as e:
            without_strip(e)

    def regions(sender, args):
        turn_on(sender.CoreWebView2 if args.IsSuccess else None)

    try:
        web_view = window.native.browser.webview
        web_view.CoreWebView2InitializationCompleted += regions
        # (it can't have started yet: that's finished by this same thread, after this; but if it
        # ever had, the event above would never come)
        if web_view.CoreWebView2 is not None:
            web_view.CoreWebView2InitializationCompleted -= regions
            turn_on(web_view.CoreWebView2)
    except Exception as e:
        without_strip(e)


def drag_strip(web_view):
    """The strip is dragged the way a title bar is (Snap, double-click to maximize, right-click for
    the window menu): the web view hands what's marked app-region: drag to Windows (WebView2 1.0.2420
    and later; older ones raise, and Windows' title bar stays)."""
    if web_view is None:
        raise RuntimeError("the web view didn't start")
    web_view.Settings.IsNonClientRegionSupportEnabled = True


def place_window(window, api=None):
    """Before the window first shows (on its own thread): put it back exactly where it was last
    time, maximized if it was, on the same screen. Windows only; elsewhere saved_geometry() does it."""
    if not winextras.WINDOWS:
        return
    try:
        form = window.native
        hwnd = int(form.Handle.ToInt64())
        WINDOW_HANDLE[:] = [hwnd]
        try:
            own_title_bar(window, api)
        except Exception as e:  # Windows' own title bar is fine too
            TIMINGS.mark(f"title bar: Windows' own ({e.__class__.__name__}: {e})")
        saved = load_settings().get("placement")
        grip = (40, 40, 30)  # the middle of Windows' title bar
        if FRAME["custom"]:
            # the page's drag strip: right across, but for the buttons at its right (and Windows'
            # sizing border, invisible just outside each side)
            # (stricter than Windows' title bar, so if the strip later can't be had, it's still fine)
            scale = winextras.dpi_scale(hwnd)
            grip = (int(48 * scale), int((CAPTION_BUTTONS + 48) * scale), int((TITLE_BAR - 8) * scale))
        if not winextras.set_placement(hwnd, saved, grip=grip):
            return
        from System.Windows.Forms import FormStartPosition, FormWindowState

        form.StartPosition = FormStartPosition.Manual  # not centred over the place just set
        if saved.get("maximized"):
            form.WindowState = FormWindowState.Maximized  # maximized on the screen it was on
    except Exception as e:  # opening somewhere else is no reason not to open
        TIMINGS.mark(f"window placement not restored: {e}")


def saved_geometry():
    """Where the window was last time, if that's still a sensible place on a screen that's there."""
    g = load_settings().get("window")
    try:
        width, height, x, y = (int(g[k]) for k in ("width", "height", "x", "y"))
    except (TypeError, KeyError, ValueError):
        return {}
    if not (760 <= width <= 6000 and 560 <= height <= 4000):
        return {}
    place = {"width": width, "height": height}
    try:
        # Only reuse the position if it's on a screen (a second monitor may have been unplugged)
        screens = webview.screens() if callable(webview.screens) else webview.screens  # a function in pywebview 6
        if any(s.x <= x + 100 < s.x + s.width and s.y <= y + 20 < s.y + s.height for s in screens):
            place.update(x=x, y=y)
    except Exception:
        pass
    return place


def main():
    TIMINGS.mark("Python and the app's code loaded")
    threading.Thread(target=watchdog, daemon=True).start()
    api = Api()
    window = webview.create_window(
        "Subtitle Maker", str(HERE / "ui" / "index.html"), js_api=api,
        # On Windows the exact place comes from place_window(); this is the size before that
        **{"width": 1040, "height": 720, **(saved_geometry() if not (winextras.WINDOWS and load_settings().get("placement")) else {})},
        min_size=(760, 560), background_color="#15140f" if dark_mode() else "#f3f1ec",
    )
    window.events.before_show += lambda: place_window(window, api)
    window.events.resized += lambda width, height: GEOMETRY.update(width=width, height=height)
    window.events.moved += lambda x, y: GEOMETRY.update(x=x, y=y)
    api._window = window
    # Videos dropped onto the "Subtitle Maker.bat" icon arrive as arguments
    opened_with = [str(Path(a).absolute()) for a in sys.argv[1:] if not a.startswith("--") and Path(a).exists()]

    window.events.shown += lambda: TIMINGS.mark("window shown (web view started)")

    def on_loaded():
        TIMINGS.mark("page loaded")
        # Dropped files only come with their full paths when Python handles the drop
        window.dom.document.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True)
        window.dom.document.events.drop += DOMEventHandler(api.on_drop, prevent_default=True, stop_propagation=True)
        if opened_with:
            api._take(opened_with)

    window.events.loaded += on_loaded
    window.events.closing += api._on_closing
    if sys.platform == "win32":
        import ctypes

        # Its own taskbar button and icon, not grouped under Python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Veyra.SubtitleMaker")
    # Keep the web view's profile between launches: a fresh private one each time is slow to make
    forget_old_page(data_dir() / "webview")
    webview.start(private_mode=False, storage_path=str(data_dir() / "webview"), icon=str(HERE / "ui" / "icon.ico"))


def forget_old_page(storage):
    """After an update, clear the web view's saved copy of the page, so the new one shows.

    pywebview serves the page from the same address every time (http://127.0.0.1:42001/index.html),
    and the web view kept a copy from 29 October 2026 that it went on showing after every update,
    even though the new file was on disk and being served. So when ui/index.html has changed
    since the last start, its caches go (only those; sign-ins and settings in the page stay).
    """
    import hashlib
    import shutil

    try:
        page = hashlib.sha256((HERE / "ui" / "index.html").read_bytes()).hexdigest()
        marker = storage / "page.txt"
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == page:
            return
        profile = storage / "EBWebView" / "Default"
        for cache in ("Cache", "Code Cache", "GPUCache", "Service Worker"):
            shutil.rmtree(profile / cache, ignore_errors=True)
        storage.mkdir(parents=True, exist_ok=True)
        marker.write_text(page, encoding="utf-8")
        TIMINGS.mark("new page since last time: cleared the web view's saved copy")
    except OSError as e:
        TIMINGS.mark(f"couldn't clear the web view's saved copy: {e}")


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
        if "--setup" in sys.argv and sys.stderr is not None:
            # Setup.bat falls back to asking its questions in the console, so no message box.
            # (Not when opened with pythonw, as the installer does: there's no console to print
            # to, so it would close without a word. The message box says what went wrong.)
            print(f"The setup window couldn't open ({e.__class__.__name__}: {e}).", file=sys.stderr)
        else:
            report_crash(e)
        sys.exit(1)
