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
