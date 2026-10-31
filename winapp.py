"""Subtitle Maker as a proper Windows app: a Start menu entry and an entry in Settings > Apps.

windows-app.ps1 does the work (a shortcut's app ID can only be set through COM). This decides
when it's needed: the app calls ensure() as it starts, which does nothing if everything is
already in place for this folder and version, so installs from before this existed (and ones
moved or updated since) sort themselves out without reinstalling.

    python winapp.py             register (install.ps1 runs this)
    python winapp.py --remove    take it away again
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "windows-app.ps1"
KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Veyra.SubtitleMaker"
WINDOWS = sys.platform == "win32"


def start_menu_link():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Subtitle Maker.lnk"


def registered():
    """{"InstallLocation", "DisplayVersion", ...} from Settings > Apps' entry, or {} if there isn't one."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            values, i = {}, 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    return values
                values[name] = value
                i += 1
    except OSError:
        return {}


def size_kb(data):
    """Roughly how much room it takes, for Settings > Apps: the program, its Python and models."""
    total = 0
    for folder in (HERE, data / "venv", data / "models"):
        for root, _, files in os.walk(folder):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    return total // 1024


def run(remove=False, version=None):
    """Run windows-app.ps1. Raises subprocess.CalledProcessError if it fails."""
    from paths import data_dir

    data = data_dir()
    args = ["-Install", str(HERE), "-Data", str(data)]
    if remove:
        args.append("-Remove")
    else:
        if version is None:
            import changelog

            version = changelog.current() or ""
        args += ["-Version", version, "-SizeKB", str(size_kb(data))]
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(SCRIPT), *args], check=True, capture_output=True, timeout=120,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def ensure():
    """Register this install if it isn't already (or is out of date). Returns what it did, for the log."""
    if not WINDOWS or not SCRIPT.exists():
        return "not Windows"
    import changelog
    import update

    if not update.installed():
        return "not an installed copy (a copy elsewhere, say on Google Drive, doesn't register)"
    version = changelog.current() or ""
    entry = registered()
    if (start_menu_link().exists() and entry.get("InstallLocation") == str(HERE)
            and entry.get("DisplayVersion") == version):
        return "already a Windows app"
    try:
        run(version=version)
    except (OSError, subprocess.SubprocessError) as e:
        detail = getattr(e, "stderr", b"") or b""
        return f"couldn't register as a Windows app: {e} {detail.decode(errors='replace').strip()[-300:]}"
    return f"registered as a Windows app ({version})"


if __name__ == "__main__":
    if WINDOWS:
        run(remove="--remove" in sys.argv)
