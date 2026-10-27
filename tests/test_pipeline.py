"""The real thing, with real downloads: Potato's Whisper model, the speaker models, a real clip.

Run with the program's Python and SUBTITLE_MAKER_HOME set to a scratch folder (see README.md).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import models  # noqa: E402
from paths import HISTORY_FILE, load_settings, save_settings  # noqa: E402

PY = sys.executable
WORK = Path(tempfile.mkdtemp(prefix="subtitle-maker-test-"))
JFK = WORK / "jfk.flac"
shutil.copy(ROOT / "tests" / "media" / "jfk.flac", JFK)
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}


def run(*args, stdin=""):
    started = time.time()
    r = subprocess.run([PY, str(ROOT / "make_subtitles.py"), *map(str, args)], input=stdin, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=1800, env=ENV)
    print(f"--- make_subtitles {' '.join(map(str, args))}  ({time.time() - started:.1f} s, code {r.returncode})")
    print(r.stdout[-2500:])
    if r.returncode:
        print(r.stderr[-2500:])
    return r


def check_srt(path, max_chars=42, max_lines=2):
    text = Path(path).read_text(encoding="utf-8")
    blocks = [b.splitlines() for b in text.strip().split("\n\n")]
    for b in blocks:
        assert re.match(r"\d\d:\d\d:\d\d,\d\d\d --> \d\d:\d\d:\d\d,\d\d\d$", b[1]), b
        lines = b[2:]
        assert 1 <= len(lines) <= max_lines and all(len(x) <= max_chars for x in lines), b
    return text


settings = load_settings()
settings.update(level="potato", language="en", review=False)
save_settings(settings)

# 1. --prepare downloads the Potato model for real, and only once
r = run("--prepare")
assert r.returncode == 0 and models.whisper_path("base"), "the Potato model didn't download"
r = run("--prepare")
assert r.returncode == 0 and "already downloaded" in r.stdout, "a second --prepare should download nothing"

# 2. A real transcription
r = run(JFK)
assert r.returncode == 0
srt = check_srt(WORK / "jfk.srt")
assert "ask not what your country can do for you" in " ".join(srt.lower().split()), srt
assert json.loads(HISTORY_FILE.read_text(encoding="utf-8"))[0]["status"] == "done"
r = run(JFK)
assert r.returncode == 0 and (WORK / "jfk (2).srt").exists(), "the second run must not overwrite the first"

# 3. Two speakers: the clip, a sped-up (higher) copy, and the clip again
from faster_whisper.audio import decode_audio  # noqa: E402

voice = decode_audio(str(JFK), sampling_rate=16000)
higher = np.interp(np.arange(0, len(voice), 1.35), np.arange(len(voice)), voice).astype(np.float32)
gap = np.zeros(16000, dtype=np.float32)
two = np.concatenate([voice, gap, higher, gap, voice])
with wave.open(str(WORK / "two.wav"), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes((np.clip(two, -1, 1) * 32767).astype("<i2").tobytes())
# Fast quality allows speakers; --model base reuses Potato's model instead of downloading Fast's
r = run(WORK / "two.wav", "--level", "fast", "--model", "base", "--speakers", "2")
assert r.returncode == 0
labelled = check_srt(WORK / "two.srt")
assert "Speaker 1:" in labelled and "Speaker 2:" in labelled, labelled
assert models.speakers_ready()

# 4. The worker keeps the model loaded between videos
worker = subprocess.Popen([PY, str(ROOT / "make_subtitles.py"), "--serve", "--events"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, env=ENV)
log, finished = [], []
for _ in range(2):
    worker.stdin.write(json.dumps({"type": "job", "argv": [str(JFK), "--events"]}) + "\n")
    worker.stdin.flush()
    for line in worker.stdout:
        if line.startswith("@@"):
            event = json.loads(line[2:])
            if event["type"] == "finished":
                finished.append(event["code"])
                break
        else:
            log.append(line.strip())
worker.stdin.close()
worker.wait(timeout=60)
print("--- worker log:\n" + "\n".join(log))
assert finished == [0, 0], finished
assert sum("Loading 'base' model" in x for x in log) == 1, "the model should load once for two videos"
assert any("already loaded" in x for x in log)

print(f"Pipeline tests passed ({WORK})")
