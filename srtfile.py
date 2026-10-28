"""Opening an existing .srt to fix it in the window: reading it, finding the video it belongs
to (so lines can be played), cutting a short clip of that video's sound, and backups.

Kept apart from make_subtitles.py so opening a file doesn't load the speech model's libraries.
"""

import io
import re
import shutil
import time
import wave
from pathlib import Path

from paths import data_dir, load_history

TIME = r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
TIMING = re.compile(rf"^\s*{TIME}\s*-->\s*{TIME}")
BACKUPS = data_dir() / "backups"
KEEP_BACKUPS = 30


class SrtError(Exception):
    """Something wrong with the file, worded for the person who picked it."""


def seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def read_srt(path):
    """[{"start", "end", "text"}] from an .srt; each subtitle's lines joined into one."""
    path = Path(path)
    if path.stat().st_size > 20 * 2**20:
        raise SrtError(f"{path.name} is too big to be a subtitle file.")
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")  # what Resolve and most tools write
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")  # older Windows tools
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip()):
        lines = block.strip().split("\n")
        timing = next((i for i, line in enumerate(lines[:3]) if TIMING.match(line)), None)
        if timing is None:
            continue
        g = TIMING.match(lines[timing]).groups()
        words = " ".join(re.sub(r"<[^>]+>", "", line).strip() for line in lines[timing + 1:]).split()
        if words:
            cues.append({"start": round(seconds(*g[:4]), 3), "end": round(seconds(*g[4:]), 3), "text": " ".join(words)})
    if not cues:
        raise SrtError(f"{path.name} doesn't have any subtitles in it, or isn't an .srt file.")
    return sorted(cues, key=lambda c: c["start"])


def video_for(srt_path, media_exts):
    """The video an .srt was made from: from History first, then one with the same name next to it."""
    srt_path = Path(srt_path)
    for item in load_history():
        if any(Path(f.get("path", "")) == srt_path for f in item.get("files", [])) and Path(item["input"]).is_file():
            return Path(item["input"])
    stem = re.sub(r" \(\d+\)$", "", srt_path.stem)  # "clip (2).srt" belongs to "clip.mp4"
    for candidate in sorted(srt_path.parent.glob("*")):
        if candidate.suffix.lower() in media_exts and candidate.stem in (srt_path.stem, stem) and candidate.is_file():
            return candidate
    return None


def clip_wav(video, start, end, rate=16000):
    """That stretch of the video's sound as .wav bytes, decoding only the part needed."""
    import av
    import numpy as np

    start, end = max(0.0, start - 0.15), end + 0.15
    chunks = []
    with av.open(str(video)) as container:
        stream = container.streams.audio[0]
        container.seek(int(start / stream.time_base), stream=stream, any_frame=False, backward=True)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        position = None
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            if position is None:
                position = float(frame.pts * stream.time_base)
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
            if float(frame.pts * stream.time_base) > end:
                break
    if position is None:
        return b""
    samples = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    first = max(0, int((start - position) * rate))
    samples = samples[first:first + int((end - start) * rate)]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2").tobytes())
    return buffer.getvalue()


def back_up(path):
    """Copy the file into the backups folder before it's replaced; the oldest backups go."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    copy = BACKUPS / f"{Path(path).stem} {time.strftime('%Y-%m-%d %H.%M.%S')}{Path(path).suffix}"
    shutil.copy2(path, copy)
    for old in sorted(BACKUPS.glob("*"), key=lambda p: p.stat().st_mtime)[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return copy
