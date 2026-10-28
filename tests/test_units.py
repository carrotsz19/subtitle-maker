"""Checks that need no network: run with the program's Python (see README.md)."""
import inspect
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app  # noqa: E402
import configure  # noqa: E402
import make_subtitles as ms  # noqa: E402
import paths  # noqa: E402
import update  # noqa: E402

# Defaults and the recommended level
assert paths.DEFAULT_SETTINGS["level"] == "high"
assert configure.recommend(("GTX 1050", 3072), 16, 4) == "high"
assert configure.recommend(("old", 1536), 16, 4) == "fast"
assert configure.recommend(None, 32, 8) == "high"
assert configure.recommend(None, 8, 4) == "potato"

# Never overwrite
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "video.srt"
    assert ms.unique_path(p) == p
    p.write_text("x")
    assert ms.unique_path(p).name == "video (2).srt"

# Subtitle lines stay inside the limits, labels included (they once pushed lines onto a third row)
ms.apply_style(paths.DEFAULT_SETTINGS)
words = ("So today I wanted to slow down and actually look at the street I walk every day, "
         "the one I never notice.").split()
toks = [ms.Token(" " + w, i * 0.1, i * 0.1 + 0.09, 0) for i, w in enumerate(words)]
for name in ("Alex", "Christopher-Jamie"):
    for cue in ms.build_cues(toks, lambda spk: ms.label("", name, "name")):
        lines = ms.wrap(ms.label(cue[2], name, "name")).splitlines()
        assert len(lines) <= ms.MAX_LINES and all(len(x) <= ms.MAX_LINE_CHARS for x in lines), lines

# Speakers: each word goes to whoever was talking, a new subtitle starts when the speaker changes,
# and voice samples pair up with the closest detected speaker (each name used once)
import speakers  # noqa: E402

turns = [(0.0, 2.0, 0), (2.5, 4.0, 1), (4.2, 6.0, 0)]
assert [speakers.speaker_at(s, e, turns) for s, e in ((0.5, 0.9), (1.9, 2.8), (3.0, 3.5), (4.05, 4.1), (9, 9.5))] == [0, 1, 1, 1, 0]
toks = [ms.Token(" " + w, s, s + 0.3, speakers.speaker_at(s, s + 0.3, turns))
        for w, s in (("Hi", 0.2), ("there.", 0.6), ("Hello", 2.6), ("back.", 3.0), ("Bye.", 4.5))]
assert [c[3] for c in ms.build_cues(toks)] == [0, 1, 0]
e = np.eye(3)
prints = {0: e[0], 1: (e[0] + e[1]) / np.sqrt(2)}
assert speakers.match_names(prints, {"Alex": e[0], "Sam": e[1]}) == {0: ("Alex", 1.0), 1: ("Sam", float(prints[1] @ e[1]))}
assert speakers.match_names(prints, {"Nobody": e[2]}) == {}

# Filler words
assert ms.is_filler(" Um,") and ms.is_filler("uh.") and not ms.is_filler("umbrella")

# Update kinds from commit messages
assert update.change("Hotfix: Window froze")["kind"] == "hotfix"
assert update.change("Security: x")["kind"] == "security"
assert update.change("Just tidying")["kind"] == "improvement"

# The GPU runs out of memory partway: carry on with the CPU from where it stopped
sr = ms.SAMPLE_RATE


class Word:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class Seg:
    def __init__(self, start, end, text):
        self.end, self.words = end, [Word(" " + text, start, end)]


class Info:
    language = "en"


class FakeModel:
    def __init__(self, fail_after):
        self.fail_after, self.calls = fail_after, []

    def transcribe(self, audio, language, **kw):
        self.calls.append((len(audio) / sr, language))

        def gen():
            for i, (s, e) in enumerate([(0.5, 2.0), (3.0, 4.0)]):
                if self.fail_after is not None and i >= self.fail_after:
                    raise RuntimeError("CUDA failed with error out of memory")
                yield Seg(s, e, f"w{i}")
        return gen(), Info()


class Bar:
    total, n = 10.0, 0.0

    def update(self, d):
        self.n += d

    def write(self, msg):
        pass


cpu_model = FakeModel(None)
ms.load_model = lambda name, device: (cpu_model, "cpu")
whisper = {"model": FakeModel(1), "device": "cuda"}
tokens, _ = ms.transcribe(whisper, "base", np.zeros(10 * sr, dtype=np.float32), None, 5, Bar())
assert whisper["device"] == "cpu" and cpu_model.calls == [(8.0, "en")]
assert [(t.word, t.start) for t in tokens] == [(" w0", 0.5), (" w0", 2.5), (" w1", 5.0)]

# Word fixes: on the words as heard, ignoring case and punctuation, several words either side
toks = [ms.Token(w, i, i + .5, 0) for i, w in enumerate([" So", " Vera,", " said", " dove", " in", " chee.", " Um", ' "vera"', " veranda"])]
fixed = ms.apply_fixes(toks, paths.clean_fixes([["vera", "Veyra"], ["dove in chee", "DaVinci"], ["um", ""], ["", "x"], ["Same", "same"]]))
assert [t.word for t in fixed] == [" So", " Veyra,", " said", " DaVinci.", ' "Veyra"', " veranda"], [t.word for t in fixed]
assert fixed[3].start == 3 and fixed[3].end == 5.5
assert paths.clean_fixes([["a", "b"], ["A", "c"], "junk", ["x"]]) == [["A", "c"]]

# Fixing an existing .srt: reading it (any line endings, a BOM, tags), finding its video, a clip, saving
import srtfile  # noqa: E402
import wave  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    with wave.open(str(d / "clip.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.sin(np.arange(16000 * 6) / 8) * 8000).astype("<i2").tobytes())
    srt = d / "clip (2).srt"
    srt.write_bytes("\ufeff1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Hello</i>\r\nthere\r\n\r\n2\r\n00:00:03.2 --> 00:00:04,000\r\nAgain\r\n".encode())
    assert srtfile.read_srt(srt) == [{"start": 1.0, "end": 2.5, "text": "Hello there"}, {"start": 3.2, "end": 4.0, "text": "Again"}]
    assert srtfile.video_for(srt, app.MEDIA_EXTS) == d / "clip.wav"
    with wave.open(__import__("io").BytesIO(srtfile.clip_wav(d / "clip.wav", 1.0, 2.5))) as w:
        assert abs(w.getnframes() / 16000 - 1.8) < 0.05
    (d / "notes.srt").write_bytes(bytes(range(256)))
    try:
        srtfile.read_srt(d / "notes.srt")
        raise AssertionError("a file with no subtitles should be refused")
    except srtfile.SrtError:
        pass
    edit = app.Api()
    opened = edit.open_srt(str(srt))
    assert opened["video"] == "clip.wav" and len(opened["cues"]) == 2
    assert edit.edit_clip(1.0, 2.5).startswith("data:audio/wav;base64,")
    saved = edit.save_srt([{"start": 1.0, "end": 2.0, "text": "Hello"}, {"start": 2.0, "end": 2.5, "text": " there "},
                           {"start": 3.2, "end": 4.0, "text": "  "}])
    assert saved["count"] == 2 and Path(saved["backup"]).read_bytes().startswith(b"\xef\xbb\xbf1")
    assert [c["text"] for c in srtfile.read_srt(srt)] == ["Hello", "there"]

# A queue cut short is offered again, minus anything that has since gone
with tempfile.TemporaryDirectory() as d:
    video = Path(d) / "v.mp4"
    video.write_bytes(b"x")
    q = app.Api()
    q.save_queue([str(video), str(Path(d) / "gone.mp4")])
    assert q._unfinished() == [str(video)]
    q.save_queue([])
    assert q._unfinished() == [] and not app.QUEUE_FILE.exists()

# The page's API must be our own functions only: pywebview walks every public attribute (and
# theirs) on the window's main thread, and walking into the window froze it for a minute
api = app.Api()
seen, exposed = set(), []


def walk(obj, base=""):
    if id(obj) in seen:
        return
    seen.add(id(obj))
    for name in dir(obj):
        if name.startswith("_"):
            continue
        attr = getattr(obj, name)
        if not getattr(attr, "_serializable", True):
            continue
        if inspect.ismethod(attr) or inspect.isfunction(attr):
            exposed.append(base + name)
        elif inspect.isclass(attr) or (not callable(attr) and hasattr(attr, "__module__")):
            walk(attr, base + name + ".")


walk(api)
nested = [e for e in exposed if "." in e]
assert exposed and not nested, nested
print(f"Unit tests passed (page API: {len(exposed)} functions)")
