"""Where the subtitle maker keeps its Python packages, models and settings on this computer.

They live outside the Tools folder (kept out of synced folders like Google Drive),
so the Tools folder stays small and can be copied anywhere. Set SUBTITLE_MAKER_HOME
to put them somewhere else, e.g. on a USB drive.
"""

import json
import os
import sys
from pathlib import Path


def data_dir():
    if os.environ.get("SUBTITLE_MAKER_HOME"):
        return Path(os.environ["SUBTITLE_MAKER_HOME"])
    if sys.platform == "win32":
        # Not AppData: packaged (MSIX) apps get AppData writes silently redirected to a private copy
        return Path.home() / ".subtitle-maker"
    return Path.home() / ".local" / "share" / "subtitle-maker"


MODELS_DIR = data_dir() / "models"
SETTINGS_FILE = data_dir() / "settings.json"
ERROR_LOG = data_dir() / "last_error.log"
HISTORY_FILE = data_dir() / "history.json"
HISTORY_LIMIT = 200

# Quality levels offered in Setup. vram_mb = measured peak extra GPU memory (int8) on a GTX 1050,
# rounded up for safety; large-v3 is an estimate. speed and accuracy are out of 5, for the small
# meters in Settings > Quality: how the levels compare with each other, not a measurement
# (High's model ran as fast as Fast's on a GTX 1050, hence the same speed).
# ram_mb = the memory the worker commits (what a memory limit in Settings > Hardware counts), on
# the processor and on the graphics card. Measured for tiny (1340 MB processor, 1300 MB graphics
# card) and High's large-v3-turbo (4160 / 2490) on a 3-minute clip on a GTX 1050; the others are
# worked out from their size between those two. All are rounded up.
LEVELS = {
    "potato":   {"model": "base", "name": "Potato", "download": "0.15 GB", "vram_mb": 400, "speed": 5, "accuracy": 1,
                 "ram_mb": {"cpu": 1600, "cuda": 1500},
                 "about": "transcription only, lightest settings, for old or slow PCs"},
    "fast":     {"model": "small", "name": "Fast", "download": "0.5 GB", "vram_mb": 700, "speed": 4, "accuracy": 2,
                 "ram_mb": {"cpu": 2300, "cuda": 1800},
                 "about": "quick, decent accuracy"},
    "balanced": {"model": "medium", "name": "Balanced", "download": "1.5 GB", "vram_mb": 1300, "speed": 2, "accuracy": 3,
                 "ram_mb": {"cpu": 4400, "cuda": 2700},
                 "about": "good accuracy, slower on CPU"},
    "high":     {"model": "large-v3-turbo", "name": "High", "download": "1.6 GB", "vram_mb": 1400, "speed": 4, "accuracy": 4,
                 "ram_mb": {"cpu": 4400, "cuda": 2700},
                 "about": "best accuracy for its speed"},
    "max":      {"model": "large-v3", "name": "Maximum", "download": "3.1 GB", "vram_mb": 2400, "speed": 1, "accuracy": 5,
                 "ram_mb": {"cpu": 7400, "cuda": 4000},
                 "about": "most accurate, slowest, needs a 3 GB+ GPU or lots of patience"},
}
# Speaker detection adds a process of its own (measured 600 MB) and the voice model in the worker
SPEAKERS_RAM_MB = 800
# The video's sound in memory (the worker's copy, the speaker step's, and decoding), per minute
AUDIO_RAM_MB_PER_MINUTE = 12

# High (large-v3-turbo) measured as fast as small and more accurate on a GTX 1050, so it is the default
# output_dir None = save each .srt next to its video
DEFAULT_SETTINGS = {
    "level": "high", "language": "en", "output_dir": None,
    # How subtitles look (movie conventions by default)
    "max_chars": 42, "max_lines": 2, "min_seconds": 1.0, "max_seconds": 6.0, "pause_split": 0.7,
    # Speakers: "name" = "Alex: Hi", "dash" = "- Hi", "none" = no labels
    "speaker_labels": "name", "speaker_files": True,
    # Accuracy: words to spell right (names, places), drop um/uh, skip music and silence, device
    "hotwords": "", "remove_fillers": False, "vad": True, "device": "auto",
    # When a video is done, and the app itself
    "transcript": False, "vtt": False, "review": True, "done_sound": True, "open_folder": True, "theme": "system",
    "auto_update": True, "channel": "main",
    # Remembered from the Make subtitles page: "Several people", and how many (0 = guess)
    "multi": False, "people": 0,
    # Word fixes applied to every video, as [heard, meant] pairs: [["vera", "Veyra"]]
    "fixes": [],
    # A Windows notification when the queue finishes while you're in another window
    "notify": True,
    # Remember the voices of people you name, to name them by themselves next time
    "remember_voices": True,
    # Things appear and go without moving (Windows' own "Show animations" setting counts too)
    "less_motion": False,
    # Coming back to a tab puts you where you'd scrolled to (off: every tab opens at its top)
    "remember_scroll": True,
    # Subtitles in English whatever language is spoken (Whisper translates as it goes)
    "translate": False,
    # Settings > Hardware (see hardware.py): how much of the processor it may use (100 = no limit),
    # letting other apps go first, a memory limit in GB (0 = none), and the graphics card's smaller
    # number format. These are the Balanced preset: as fast as 0.3.0 while nothing else is running,
    # and other apps get the processor first when they want it.
    "cpu_percent": 100, "low_priority": True, "memory_limit_gb": 0, "gpu_low_memory": False,
}
# large-v3-turbo was only trained to transcribe: asked to translate, it writes the original
# language anyway (OpenAI's Whisper README says so). The level with it translates with this one.
TRANSLATE_WITH = {"large-v3-turbo": "medium"}
MAX_FIXES = 200
# Every language Whisper knows (faster_whisper.tokenizer._LANGUAGE_CODES; a test checks they match).
# The window lists them by name; anything else in the settings goes back to the default.
LANGUAGES = (
    "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo", "br", "bs", "ca", "cs", "cy", "da", "de", "el", "en",
    "es", "et", "eu", "fa", "fi", "fo", "fr", "gl", "gu", "ha", "haw", "he", "hi", "hr", "ht", "hu", "hy", "id", "is",
    "it", "ja", "jw", "ka", "kk", "km", "kn", "ko", "la", "lb", "ln", "lo", "lt", "lv", "mg", "mi", "mk", "ml", "mn",
    "mr", "ms", "mt", "my", "ne", "nl", "nn", "no", "oc", "pa", "pl", "ps", "pt", "ro", "ru", "sa", "sd", "si", "sk",
    "sl", "sn", "so", "sq", "sr", "su", "sv", "sw", "ta", "te", "tg", "th", "tk", "tl", "tr", "tt", "uk", "ur", "uz",
    "vi", "yi", "yo", "zh", "yue",
)
# The values each setting may take; anything else (a hand-edited file, an old version) goes back to the default
SETTING_CHOICES = {
    "max_chars": (32, 37, 42, 50), "max_lines": (1, 2), "min_seconds": (0.7, 1.0, 1.5),
    "max_seconds": (4.0, 6.0, 8.0), "pause_split": (0.4, 0.7, 1.2), "speaker_labels": ("name", "dash", "none"),
    "speaker_files": (True, False), "remove_fillers": (True, False), "vad": (True, False),
    "device": ("auto", "cuda", "cpu"), "transcript": (True, False), "vtt": (True, False), "review": (True, False),
    "done_sound": (True, False), "multi": (True, False), "people": (0, 2, 3, 4, 5, 6),
    "open_folder": (True, False), "theme": ("system", "light", "dark"), "auto_update": (True, False),
    "notify": (True, False), "remember_voices": (True, False), "less_motion": (True, False),
    "remember_scroll": (True, False),
    "channel": ("main", "experimental"), "translate": (True, False),
    "cpu_percent": (25, 50, 75, 100), "low_priority": (True, False),
    "memory_limit_gb": (0, 2, 3, 4, 6, 8, 12, 16, 24, 32), "gpu_low_memory": (True, False),
}


def load_settings():
    try:
        settings = {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        settings = dict(DEFAULT_SETTINGS)
    if settings["level"] not in LEVELS:
        settings["level"] = DEFAULT_SETTINGS["level"]
    if settings["language"] is not None and settings["language"] not in LANGUAGES:
        settings["language"] = DEFAULT_SETTINGS["language"]
    for key, allowed in SETTING_CHOICES.items():
        if settings[key] not in allowed:
            settings[key] = DEFAULT_SETTINGS[key]
    if not isinstance(settings["hotwords"], str):
        settings["hotwords"] = ""
    settings["hotwords"] = settings["hotwords"][:500]
    settings["fixes"] = clean_fixes(settings["fixes"])
    return settings


def clean_fixes(fixes):
    """Word fixes as [heard, meant] pairs of text, without blanks or repeats (a later one wins)."""
    pairs = {}
    for pair in fixes if isinstance(fixes, list) else []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(x, str) for x in pair):
            heard, meant = " ".join(pair[0].split())[:80], " ".join(pair[1].split())[:80]
            if heard and heard.lower() != meant.lower():
                pairs.pop(heard.lower(), None)
                pairs[heard.lower()] = [heard, meant]
    return list(pairs.values())[-MAX_FIXES:]


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


def save_settings(settings):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def level_for_model(model):
    """The LEVELS entry that uses this Whisper model, or None for models not offered in Setup."""
    return next((lv for lv in LEVELS.values() if lv["model"] == model), None)


def load_history():
    """Past runs, newest first."""
    try:
        items = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return items if isinstance(items, list) else []


def save_history(items):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(items[:HISTORY_LIMIT], indent=1), encoding="utf-8")


def add_history(entry):
    save_history([entry] + load_history())
