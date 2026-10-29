"""The version number and what's new in each version, read from CHANGELOG.md.

CHANGELOG.md has a "## 0.2.0 · 29 October 2026" heading per version, newest first, each with
a "- " line per change. The first heading is the version of the files in this folder.
"""

import re
from pathlib import Path

from paths import data_dir

HERE = Path(__file__).resolve().parent
CHANGELOG = HERE / "CHANGELOG.md"
SEEN_FILE = data_dir() / "seen_version.txt"  # the newest version whose What's new was shown
HEADING = re.compile(r"^##\s+v?(\d+(?:\.\d+)*)\s*(?:[·-]\s*(.+?))?\s*$")


def versions(text=None):
    """[{"version", "date", "changes": [str]}], newest first."""
    if text is None:
        try:
            text = CHANGELOG.read_text(encoding="utf-8")
        except OSError:
            return []
    found = []
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            found.append({"version": heading.group(1), "date": heading.group(2), "changes": []})
        elif found and line.startswith("- "):
            found[-1]["changes"].append(line[2:].strip())
        elif found and found[-1]["changes"] and line.startswith("  ") and line.strip():
            found[-1]["changes"][-1] += " " + line.strip()  # a change written over two lines
    return found


def current():
    """This folder's version number ("0.2.0"), or None if CHANGELOG.md is missing."""
    listed = versions()
    return listed[0]["version"] if listed else None


def _key(version):
    return tuple(int(x) for x in version.split("."))


def news(first_run=False):
    """The versions whose changes haven't been shown yet, newest first.

    A new install starts with nothing to show (all of it is new). Someone who updated from a
    version before this file existed is shown the current version."""
    listed = versions()
    if not listed:
        return []
    try:
        seen = SEEN_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        seen = None
    if first_run:
        mark_seen(listed[0]["version"])
        return []
    if seen is None:
        return listed[:1]
    try:
        return [v for v in listed if _key(v["version"]) > _key(seen)]
    except ValueError:  # a hand-edited file
        return listed[:1]


def mark_seen(version=None):
    version = version or current()
    if version:
        SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        SEEN_FILE.write_text(version, encoding="utf-8")
