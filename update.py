"""Checks the Subtitle Maker GitHub repo for a newer version and installs it into this folder.

    python update.py               check, show what's new, ask, then update
    python update.py --yes         update without asking
    python update.py --check       just say whether there is an update
    python update.py --channel experimental   switch to the experimental version
    python update.py --channel main           go back to the stable version

Only the program files are replaced. Your Voices clips, settings and downloaded models are
never touched. The repo is public, so no GitHub account is needed.
"""

import argparse
import hashlib
import io
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from paths import data_dir, load_settings, save_settings

HERE = Path(__file__).parent
REPO = os.environ.get("SUBTITLE_MAKER_REPO", "carrotsz19/subtitle-maker")
# Which version to follow: the stable one everybody gets, or the experimental one (newer, less
# tested). Each is a branch of the repo; Settings > Updates switches between them.
CHANNELS = {"main": "Stable", "experimental": "Experimental"}
API = os.environ.get("SUBTITLE_MAKER_API", "https://api.github.com") + "/repos/" + REPO  # override for testing
INSTALLED_FILE = data_dir() / "installed.json"


class UpdateError(Exception):
    """Something the user can act on, already worded for them."""


def fetch(url, accept="application/vnd.github+json", progress=None):
    """The response body. progress(done_mb, total_mb or None) is called as it arrives."""
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "subtitle-maker-updater",
                                                   "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if not progress:
                return response.read()
            size = int(response.headers.get("Content-Length") or 0) / 2**20 or None
            chunks = []
            while chunk := response.read(256 * 1024):
                chunks.append(chunk)
                progress(sum(map(len, chunks)) / 2**20, size)
            return b"".join(chunks)
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


# What kind of update a change is, from the start of its commit message ("Security: ..."), most
# serious first. The app makes more noise the more serious the most serious waiting change is.
# "Critical:" is a security problem bad enough that older versions lock until they're updated.
KINDS = ("critical", "security", "hotfix", "feature", "improvement")
KIND_WORDS = {"critical": "critical", "security": "security", "hotfix": "hotfix", "fix": "hotfix",
              "feature": "feature", "improvement": "improvement"}
REQUIRED_FILE = data_dir() / "required_update.json"  # a critical update was found: locked until it's in


def required():
    """{"text", "sha", "found"} while a critical update is waiting to be installed, else None.
    Kept on disk, so restarting (or being offline) doesn't get round it."""
    try:
        data = json.loads(REQUIRED_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def change(message):
    """{"kind", "text"} for one commit: "Hotfix: Window froze" -> hotfix, "Window froze"."""
    line = message.splitlines()[0].strip()
    m = re.match(r"(\w+)\s*:\s*(.+)", line)
    if m and m.group(1).lower() in KIND_WORDS:
        return {"kind": KIND_WORDS[m.group(1).lower()], "text": m.group(2)}
    return {"kind": "improvement", "text": line}


def channel():
    """The branch Settings says to follow."""
    return load_settings().get("channel", "main")


def check(branch=None):
    """What's on GitHub compared with what's installed. Raises UpdateError.

    "switch" is set when that means changing channel (the installed copy came from the other
    branch), and "back" when the version it moves to is older than the installed one.
    """
    branch = branch or channel()
    try:
        commit = fetch_json(f"/commits/{branch}")
    except UpdateError:
        if branch == "main":
            raise
        raise UpdateError("There's no experimental version on GitHub right now. Switch back to Stable in Settings.") from None
    latest = {"sha": commit["sha"], "date": commit["commit"]["committer"]["date"],
              "message": commit["commit"]["message"].splitlines()[0], "branch": branch}
    current = installed()
    switch = branch if current and current.get("branch", "main") != branch else None
    news, back = [], False
    if current and current["sha"] != latest["sha"]:
        try:
            compare = fetch_json(f"/compare/{current['sha']}...{latest['sha']}")
            news = [change(c["commit"]["message"]) for c in compare.get("commits", [])]
            back = compare.get("status") == "behind"
        except UpdateError:
            pass  # the list of changes is a nicety; the update itself still works
    news = news or [change(latest["message"])]
    # Most serious first, then newest first; ten is plenty to read
    news = sorted(reversed(news), key=lambda n: KINDS.index(n["kind"]))
    more = max(0, len(news) - 10)  # the window says "and 4 more", so a big update doesn't look small
    news = news[:10]
    status = {"repo": REPO, "latest": latest, "installed": current, "branch": branch, "switch": switch, "back": back,
              "update": not current or current["sha"] != latest["sha"] or bool(switch), "news": news, "more": more,
              "kind": news[0]["kind"]}
    if status["update"] and status["kind"] == "critical":
        REQUIRED_FILE.write_text(json.dumps({"text": news[0]["text"], "sha": latest["sha"],
                                             "found": latest["date"]}), encoding="utf-8")
    elif not status["update"]:
        REQUIRED_FILE.unlink(missing_ok=True)  # up to date: nothing can be waiting
    status["required"] = required()
    return status


def program_files(root):
    """Files that belong to the program, relative to root: everything except people's own voice clips."""
    files = []
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root)
        # tests/ and .github/ are for developing it, not part of an install
        if not path.is_file() or rel.parts[0] in (".git", ".github", "tests", "__pycache__") or "__pycache__" in rel.parts:
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


def record(sha, date=None, files=None, branch="main"):
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
    INSTALLED_FILE.write_text(json.dumps({"sha": sha, "date": date, "folder": str(HERE.resolve()), "branch": branch,
                                          "files": files}, indent=2), encoding="utf-8")
    FILES_LIST.unlink(missing_ok=True)
    REQUIRED_FILE.unlink(missing_ok=True)  # the newest version is in, so whatever was required is too


def print_step(step, done=None, total=None):
    """The default way apply() reports progress: a line per step, in a console."""
    if done is None:
        print({"download": "Downloading the new version...", "install": "Installing it...",
               "packages": "Updating Python packages (a minute or two)...", "done": "Updated."}[step])


def inside(rel):
    """HERE / rel, or None if rel would point outside this folder (installed.json is only a file)."""
    target = (HERE / rel).resolve()
    return target if target.is_relative_to(HERE.resolve()) and target != HERE.resolve() else None


NEW, OLD = ".update-new", ".update-old"  # endings of the files an update is swapping


def swap_in(new_root, files, progress=lambda i: None):
    """Put these files from new_root into this folder, all or nothing.

    Copying straight over the old files one by one meant a failure partway (antivirus holding a
    file, a full disk) left half the old version and half the new, which could stop it opening
    to update again. So every new file is copied in alongside first (name.update-new); only once
    they all are does each replace its old one, and if a replacement fails the ones already done
    are put back.
    """
    targets = []
    for rel in files:
        target = inside(rel)
        if not target:
            raise UpdateError(f"The download had a file outside its folder ({rel}). Nothing was changed.")
        targets.append((new_root / rel, target))
    staged = []
    try:
        for source, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            staged.append(target.with_name(target.name + NEW))
            shutil.copy2(source, staged[-1])
    except OSError as e:
        for path in staged:
            path.unlink(missing_ok=True)
        raise UpdateError(f"Couldn't write the new files ({e.strerror or e}). Nothing was changed.") from e
    done = []  # (target, its old version's backup or None)
    try:
        for i, ((_, target), new) in enumerate(zip(targets, staged), 1):
            backup = None
            if target.exists():
                backup = target.with_name(target.name + OLD)
                os.replace(target, backup)
            done.append((target, backup))
            os.replace(new, target)
            progress(i)
    except OSError as e:
        for target, backup in reversed(done):
            try:
                if backup:
                    os.replace(backup, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                pass
        for path in staged:
            path.unlink(missing_ok=True)
        raise UpdateError(f"Couldn't replace {target.name} ({e.strerror or e}); the old version was put back.") from e
    for _, backup in done:
        if backup:
            backup.unlink(missing_ok=True)


def apply(latest, report=print_step, packages=True):
    """Download that version and put it in this folder.

    report(step, done, total) hears each step: download (MB), install (files), packages, done.
    With packages=False (the app window, whose own packages Windows keeps locked), changed
    packages are left for finish() to install after the window has closed. Returns the
    requirements files whose packages still need installing.
    """
    report("download")
    data = fetch(f"{API}/zipball/{latest['sha']}", accept="application/vnd.github+json",
                 progress=lambda done, total: report("download", done, total))
    old_requirements = {name: file_hash(HERE / name) for name in ("requirements.txt", "requirements-gpu.txt")}
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmp)
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(roots) != 1 or not (roots[0] / "make_subtitles.py").exists():
            raise UpdateError("The download didn't look like Subtitle Maker. Nothing was changed.")
        new_root = roots[0]
        new_files = program_files(new_root)
        report("install", 0, len(new_files))
        swap_in(new_root, new_files, lambda i: report("install", i, len(new_files)))
        # Program files the new version no longer has (never anything else, and never outside this folder)
        previous = installed()
        for rel in set(previous["files"] if previous else []) - set(new_files):
            target = inside(rel)
            if target and target.is_file():
                target.unlink()
    record(latest["sha"], latest.get("date"), new_files, latest.get("branch", "main"))

    changed = [name for name, digest in old_requirements.items() if file_hash(HERE / name) != digest
               and (name == "requirements.txt" or shutil.which("nvidia-smi"))]
    if changed and not packages:
        PENDING_FILE.write_text("\n".join(changed), encoding="utf-8")
        report("done")
        return changed
    if changed:
        report("packages")
        for name in changed:
            pip(name)
    report("done")
    return []


PENDING_FILE = data_dir() / "update-packages.txt"  # packages an update left for after the window closed


def finish(wait_pid, reopen=True):
    """After the window closes: install any packages the update left pending, then reopen it."""
    wait_for_exit(wait_pid)
    try:
        pending = [n for n in PENDING_FILE.read_text(encoding="utf-8").split() if n]
    except OSError:
        pending = []
    if pending:
        print("Finishing the update: installing new Python packages (a minute or two)...")
        try:
            for name in pending:
                pip(name, quiet=False)
            PENDING_FILE.unlink(missing_ok=True)
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"That didn't work ({e}). Run Setup.bat to fix it.")
            input("Press Enter to close.")
            return
    if reopen:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        subprocess.Popen([str(pythonw if pythonw.exists() else sys.executable), str(HERE / "app.py")], cwd=str(HERE),
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def wait_for_exit(pid, seconds=60):
    """Wait until the program with this process id has closed (or give up after a minute)."""
    import time

    end = time.time() + seconds
    while time.time() < end:
        if sys.platform == "win32":
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
            if not handle:
                return
            ctypes.windll.kernel32.WaitForSingleObject(handle, int((end - time.time()) * 1000))
            ctypes.windll.kernel32.CloseHandle(handle)
            return
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.3)


def pip(requirements, quiet=True):
    subprocess.run([sys.executable, "-m", "pip", "install", *(["--quiet"] if quiet else []), "-r", str(HERE / requirements)],
                   check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if quiet else 0)


def describe(version):
    return f"{version['sha'][:7]} ({version['date'][:10]})" if version and version.get("date") else \
        (version["sha"][:7] if version else "unknown")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only check, don't update")
    parser.add_argument("--yes", action="store_true", help="update without asking")
    parser.add_argument("--channel", choices=list(CHANNELS), help="follow this version from now on")
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)  # for the app window
    parser.add_argument("--record", metavar="SHA", help=argparse.SUPPRESS)  # used by the installer
    parser.add_argument("--finish", type=int, metavar="PID", help=argparse.SUPPRESS)  # used by the app window
    args = parser.parse_args()
    if args.record:
        record(args.record, branch=args.channel or "main")
        if args.channel:  # the installer's choice is also the one to follow from now on
            save_settings({**load_settings(), "channel": args.channel})
        return
    if args.finish:
        finish(args.finish)
        return

    if args.channel:
        save_settings({**load_settings(), "channel": args.channel})
    interactive = sys.stdin.isatty() and not args.json
    try:
        status = check()
    except UpdateError as e:
        return fail(args, error=str(e))

    if args.json:
        print(json.dumps(status))
        return
    print(f"Installed: {describe(status['installed'])}\nLatest:    {describe(status['latest'])} ({CHANNELS[status['branch']]})")
    if not status["update"]:
        print("You have the latest version.")
        return
    if status["switch"]:
        print(f"This switches to the {CHANNELS[status['switch']].lower()} version.")
    print("What's new:\n" + "\n".join(f"  - [{n['kind']}] {n['text']}" for n in status["news"]))
    if status["kind"] == "critical":
        print("This is an important security update: Subtitle Maker can't be used until it's installed.")
    elif status["kind"] == "security":
        print("This includes a security update: please install it.")
    if args.check:
        return
    if not args.yes and interactive and input("Update now? [Y/n] ").strip().lower() not in ("", "y", "yes"):
        return
    try:
        apply(status["latest"])
    except (UpdateError, OSError, subprocess.CalledProcessError) as e:
        return fail(args, error=f"The update didn't finish: {e}")


def fail(args, error):
    if args.json:
        print(json.dumps({"error": error}))
    else:
        sys.exit(error)


if __name__ == "__main__":
    main()
