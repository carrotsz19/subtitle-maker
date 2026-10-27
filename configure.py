"""Asks which quality level and language to use, and saves the choice. Run by Setup and Settings."""

import os
import subprocess

import psutil

from paths import LEVELS, SETTINGS_FILE, load_settings, save_settings

LANGUAGES = [("en", "English"), (None, "Auto-detect (any language)")]


def nvidia_gpu():
    """(name, total MB) of the first NVIDIA GPU with a working driver, or None."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.splitlines()[0]
        name, total = out.rsplit(",", 1)
        return name.strip(), int(total)
    except Exception:
        return None


def recommend(gpu, ram_gb, cores):
    if gpu:
        # Leave ~0.5 GB for Windows and other apps
        usable = gpu[1] - 500
        for level in ("high", "balanced", "fast"):
            if LEVELS[level]["vram_mb"] <= usable:
                return level
        return "potato"
    if ram_gb < 8 or cores <= 4:
        return "potato"
    # large-v3-turbo has only 4 decoder layers, so on a strong CPU it should keep up with medium (not measured)
    return "fast" if ram_gb < 16 or cores < 8 else "high"


def ask(prompt, options, default):
    """Numbered menu; returns the chosen option's key. Enter picks the default."""
    for i, (_, label) in enumerate(options, 1):
        print(f"  {i}) {label}")
    keys = [k for k, _ in options]
    while True:
        try:
            answer = input(f"{prompt} [Enter = {keys.index(default) + 1}]: ").strip()
        except EOFError:
            return default
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return keys[int(answer) - 1]
        print("  Type one of the numbers above.")


def main():
    settings = load_settings()
    gpu = nvidia_gpu()
    ram_gb = psutil.virtual_memory().total / 2**30
    cores = psutil.cpu_count(logical=False) or os.cpu_count() or 2

    print("\nThis PC:", f"{gpu[0]} ({gpu[1] / 1024:.0f} GB)" if gpu else "no NVIDIA GPU (runs on the CPU)",
          f"| {ram_gb:.0f} GB RAM | {cores} CPU cores")
    best = recommend(gpu, ram_gb, cores)
    # Keep the current choice as the default when re-running, but still flag the recommendation
    default = settings["level"] if SETTINGS_FILE.exists() else best

    print("\nWhich quality level?")
    options = []
    for key, lv in LEVELS.items():
        label = f"{lv['name']:<9} {lv['about']}  ({lv['download']} download"
        if gpu:
            label += f", ~{lv['vram_mb'] / 1024:.1f} GB GPU memory"
        label += ")"
        if key == best:
            label += "   <- recommended for this PC"
        elif key == default:
            label += "   (current)"
        options.append((key, label))
    settings["level"] = ask("Choose", options, default)

    print("\nWhich language is spoken in your videos?")
    lang_default = settings["language"] if any(k == settings["language"] for k, _ in LANGUAGES) else "other"
    lang_options = LANGUAGES + [("other", "Another language (type its code, e.g. zh, ms, ta, ja)")]
    choice = ask("Choose", lang_options, lang_default)
    if choice == "other":
        try:
            choice = input("Language code: ").strip().lower() or None
        except EOFError:
            choice = None
    settings["language"] = choice

    save_settings(settings)
    lv = LEVELS[settings["level"]]
    print(f"\nSaved: {lv['name']} ({lv['model']} model), language: {settings['language'] or 'auto-detect'}.")
    if settings["level"] == "potato":
        print("Potato mode: transcription only. Speaker detection and the resource readout are turned off.")


if __name__ == "__main__":
    main()
