"""Checks the Subtitle Maker GitHub repo for a newer version and installs it into this folder.

    python update.py               check, show what's new, ask, then update
    python update.py --yes         update without asking
    python update.py --check       just say whether there is an update

Only the program files are replaced. Your Voices clips, settings and downloaded models are
never touched. The repo is public, so no GitHub account is needed.
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from paths import data_dir

HERE = Path(__file__).parent
REPO = os.environ.get("SUBTITLE_MAKER_REPO", "carrotsz19/subtitle-maker")
BRANCH = "main"
API = os.environ.get("SUBTITLE_MAKER_API", "https://api.github.com") + "/repos/" + REPO  # override for testing
INSTALLED_FILE = data_dir() / "installed.json"


class UpdateError(Exception):
    """Something the user can act on, already worded for them."""


def fetch(url, accept="application/vnd.github+json"):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "subtitle-maker-updater",
                                                   "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
            raise UpdateError("GitHub says too many update checks from this network. Try again in an hour.") from e
        if e.code == 404:
            raise UpdateError(f"Couldn't find {REPO} on GitHub. It may have been renamed or made private.") from e
        raise UpdateError(f"GitHub answered {e.code} {e.reason}. Try again later.") from e
    except urllib.error.URLError as e:
        raise UpdateError(f"Couldn't reach GitHub ({e.reason}). Check your internet connection.") from e


def fetch_json(path):
    return json.loads(fetch(API + path))


def installed():
    """{"sha", "date", "files": [...]} for the version in this folder, or None if unknown."""
    try:
        version = json.loads(INSTALLED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # The record is about one folder; a second copy elsewhere (say, on Google Drive) is unknown
    return version if version.get("folder") == str(HERE.resolve()) else None


def check():
    """What's on GitHub compared with what's installed. Raises UpdateError."""
    commit = fetch_json(f"/commits/{BRANCH}")
    latest = {"sha": commit["sha"], "date": commit["commit"]["committer"]["date"],
              "message": commit["commit"]["message"].splitlines()[0]}
    current = installed()
    news = []
    if current and current["sha"] != latest["sha"]:
        try:
            compare = fetch_json(f"/compare/{current['sha']}...{latest['sha']}")
            news = [c["commit"]["message"].splitlines()[0] for c in compare.get("commits", [])][-10:]
        except UpdateError:
            pass  # the list of changes is a nicety; the update itself still works
    return {"repo": REPO, "latest": latest, "installed": current,
            "update": not current or current["sha"] != latest["sha"], "news": news or [latest["message"]]}


def program_files(root):
    """Files that belong to the program, relative to root: everything except people's own voice clips."""
    files = []
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or rel.parts[0] in (".git", "__pycache__") or "__pycache__" in rel.parts:
            continue
        if rel.parts[0] == "Voices" and rel.name != "README.txt":
            continue
        files.append(rel.as_posix())
    return files


def file_hash(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


FILES_LIST = data_dir() / "install-files.txt"  # written by Install Subtitle Maker.bat


def record(sha, date=None, files=None):
    """Remember which version this folder now has, and exactly which files came with it.

    The file list is what updates replace and Uninstall removes, so it must be the downloaded
    files only, never whatever else happens to be in the folder.
    """
    if files is None:
        try:
            files = [line.strip() for line in FILES_LIST.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        except OSError:
            files = []  # unknown: better to remove too little than something of yours
    INSTALLED_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTALLED_FILE.write_text(json.dumps({"sha": sha, "date": date, "folder": str(HERE.resolve()),
                                          "files": files}, indent=2), encoding="utf-8")
    FILES_LIST.unlink(missing_ok=True)


def apply(latest, say=print):
    """Download that version and put it in this folder."""
    say("Downloading the new version...")
    data = fetch(f"{API}/zipball/{latest['sha']}", accept="application/vnd.github+json")
    old_requirements = {name: file_hash(HERE / name) for name in ("requirements.txt", "requirements-gpu.txt")}
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmp)
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(roots) != 1 or not (roots[0] / "make_subtitles.py").exists():
            raise UpdateError("The download didn't look like Subtitle Maker. Nothing was changed.")
        new_root = roots[0]
        new_files = program_files(new_root)
        say(f"Installing {len(new_files)} files...")
        for rel in new_files:
            target = HERE / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(new_root / rel, target)
        # Program files the new version no longer has (never anything else)
        previous = installed()
        for rel in set(previous["files"] if previous else []) - set(new_files):
            if (HERE / rel).is_file():
                (HERE / rel).unlink()
    record(latest["sha"], latest.get("date"), new_files)

    changed = [name for name, digest in old_requirements.items() if file_hash(HERE / name) != digest]
    if "requirements.txt" in changed:
        say("Updating Python packages (a minute or two)...")
        pip("requirements.txt")
    if "requirements-gpu.txt" in changed and shutil.which("nvidia-smi"):
        pip("requirements-gpu.txt")
    say("Updated.")


def pip(requirements):
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "-r", str(HERE / requirements)],
                   check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def describe(version):
    return f"{version['sha'][:7]} ({version['date'][:10]})" if version and version.get("date") else \
        (version["sha"][:7] if version else "unknown")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only check, don't update")
    parser.add_argument("--yes", action="store_true", help="update without asking")
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)  # for the app window
    parser.add_argument("--record", metavar="SHA", help=argparse.SUPPRESS)  # used by the installer
    args = parser.parse_args()
    if args.record:
        record(args.record)
        return

    interactive = sys.stdin.isatty() and not args.json
    try:
        status = check()
    except UpdateError as e:
        return finish(args, error=str(e))

    if args.json:
        print(json.dumps(status))
        return
    print(f"Installed: {describe(status['installed'])}\nLatest:    {describe(status['latest'])}")
    if not status["update"]:
        print("You have the latest version.")
        return
    print("What's new:\n" + "\n".join(f"  - {line}" for line in status["news"]))
    if args.check:
        return
    if not args.yes and interactive and input("Update now? [Y/n] ").strip().lower() not in ("", "y", "yes"):
        return
    try:
        apply(status["latest"])
    except (UpdateError, OSError, subprocess.CalledProcessError) as e:
        return finish(args, error=f"The update didn't finish: {e}")


def finish(args, error):
    if args.json:
        print(json.dumps({"error": error}))
    else:
        sys.exit(error)


if __name__ == "__main__":
    main()
