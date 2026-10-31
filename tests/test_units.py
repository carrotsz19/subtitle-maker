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

# Who said each sentence: whole-sentence fingerprints repair the detector lumping two people
# together, never give more people than asked for, and voice clips name them
class FakeDetector:
    """Fingerprints from the audio itself: speaker A's audio is +0.1, B's is -0.1 (plus noise)."""
    def fingerprint(self, audio):
        rng = np.random.default_rng(int(abs(audio.sum()) * 1000) % 2**32)
        v = (np.array([1.0, 0, 0, 0]) if audio.mean() > 0 else np.array([0, 1.0, 0, 0])) + rng.normal(0, 0.15, 4)
        return v / np.linalg.norm(v)


def said(plan):
    """[(who, seconds)] -> audio, tokens (a sentence per turn, a word every 0.4 s), truth per word."""
    audio, toks, truth, t = [], [], [], 0.0
    for who, length in plan:
        n = int(length / 0.4)
        audio.append(np.full(int(length * 16000), 0.1 if who == "A" else -0.1, dtype=np.float32))
        toks += [ms.Token(" word" + ("." if i == n - 1 else ""), t + i * 0.4, t + i * 0.4 + 0.35, None) for i in range(n)]
        truth += [who] * n
        t += length
    return np.concatenate(audio), toks, truth


plan = [("A", 3.2), ("B", 2.4), ("A", 4.0), ("B", 3.2), ("B", 0.4), ("A", 2.8), ("B", 3.6)]
audio, toks, truth = said(plan)
lumped = [(0.0, 9.6, 0), (9.6, 19.6, 1)]  # the detector got the first half wrong
fixed, new_turns, names = speakers.assign(toks, lumped, FakeDetector(), audio, 2)
groups = {}
for t, who in zip(fixed, truth):
    groups.setdefault(who, set()).add(t.speaker)
assert groups["A"].isdisjoint(groups["B"]) and len(groups["A"]) == len(groups["B"]) == 1, groups
assert fixed[0].speaker == 0 and names is None and {s for _, _, s in new_turns} == {0, 1}
assert len({t.speaker for t in speakers.assign(toks, lumped, FakeDetector(), audio, -1)[0]}) == 2  # guessing finds 2
clips = {"Jamie": FakeDetector().fingerprint(np.full(16000, -0.1, np.float32)), "Alex": FakeDetector().fingerprint(np.full(16000, 0.1, np.float32))}
fixed, _, names = speakers.assign(toks, lumped, FakeDetector(), audio, 2, clips)
assert {names[t.speaker] for t, who in zip(fixed, truth) if who == "A"} == {"Alex"}, names
assert {names[t.speaker] for t, who in zip(fixed, truth) if who == "B"} == {"Jamie"}, names
# Whisper running on across a change of speaker (no full stop): long "sentences" are split
run_on = [t._replace(word=t.word.rstrip(".")) for t in toks]
spans = speakers.sentence_spans(run_on, [0] * len(run_on))
assert all(run_on[b - 1].end - run_on[a].start <= speakers.LONGEST for a, b in spans)
assert speakers.assign(toks, [], FakeDetector(), audio, 2) == (toks, [], None)

# Fingerprints worked out during transcription give the same answer, with the work already done
class Counting(FakeDetector):
    calls = 0

    def fingerprint(self, audio):
        Counting.calls += 1
        return super().fingerprint(audio)


direct = speakers.assign(toks, lumped, FakeDetector(), audio, 2)[0]
early = speakers.Fingerprints(Counting(), audio, lambda: lumped)
for n in range(5, len(toks) + 1, 5):  # as Whisper hands over more and more words, now and then
    early.ahead(toks[:n])
    __import__("time").sleep(0.05)
early.finish()
before = Counting.calls
assert [t.speaker for t in speakers.assign(toks, lumped, early, audio, 2)[0]] == [t.speaker for t in direct]
assert early.ahead_count > 0 and Counting.calls - before <= 2, (early.ahead_count, Counting.calls - before)

# Remembered voices: a confident match names someone; a lookalike or a near-tie doesn't
import known_voices  # noqa: E402

known_voices.forget_all()
alex, jamie = np.array([1.0, 0, 0, 0]), np.array([0, 1.0, 0, 0])
known_voices.remember({"Alex": alex, "Jamie": jamie}, "model-a")
known_voices.remember({"Alex": np.array([0.9, 0.1, 0, 0]) / np.linalg.norm([0.9, 0.1])}, "model-a")
assert [(r["name"], r["videos"]) for r in sorted(known_voices.listing(), key=lambda r: r["name"])] == [("Alex", 2), ("Jamie", 1)]
found = known_voices.match({0: np.array([0.95, 0.05, 0.3, 0]) / np.linalg.norm([0.95, 0.05, 0.3]), 1: np.array([0, 0, 0, 1.0])}, "model-a")
assert {spk: name for spk, (name, _) in found.items()} == {0: "Alex"}, found  # nobody sounds like speaker 1
assert known_voices.match({0: alex}, "model-b") == {}  # another model's fingerprints don't compare
assert known_voices.match({0: np.array([1.0, 1.0, 0, 0]) / np.sqrt(2)}, "model-a") == {}  # a tie: ask instead
assert known_voices.match({0: alex}, "model-a", skip={"Alex"}) == {}
assert [r["name"] for r in known_voices.forget("Alex")] == ["Jamie"]
known_voices.forget_all()

# Filler words
assert ms.is_filler(" Um,") and ms.is_filler("uh.") and not ms.is_filler("umbrella")

# Update kinds from commit messages
assert update.change("Hotfix: Window froze")["kind"] == "hotfix"
assert update.change("Security: x")["kind"] == "security"
assert update.change("Just tidying")["kind"] == "improvement"

# An important ("Critical:") security update locks this version until it's installed: the lock is
# kept on disk (restarts and being offline don't lift it), and making subtitles refuses meanwhile
assert update.change("Critical: Unsafe model download")["kind"] == "critical"
replies = {"/commits/main": {"sha": "new", "commit": {"message": "Tidy", "committer": {"date": "2026-10-29"}}},
           "/compare/old...new": {"status": "ahead", "commits": [{"commit": {"message": "Critical: Unsafe download"}},
                                                                  {"commit": {"message": "Tidy"}}]}}
real_fetch, real_installed = update.fetch_json, update.installed
update.fetch_json = lambda path: replies[path]
update.installed = lambda: {"sha": "old", "date": None, "files": []}
status = update.check("main")
assert status["kind"] == "critical" and status["required"]["text"] == "Unsafe download", status
try:
    ms.main(["clip.mp4"])
    raise AssertionError("making subtitles should refuse while locked")
except SystemExit as e:
    assert "security update" in str(e.code), e.code
update.fetch_json = lambda path: (_ for _ in ()).throw(update.UpdateError("offline"))
assert update.required(), "the lock stays when the next check can't reach GitHub"
# ...but offline it carries on (there's no way to update, and no risk): it gets as far as the missing video
try:
    ms.main(["clip.mp4"])
except SystemExit as e:
    assert "security update" not in str(e.code), e.code
assert update.required(), "carrying on offline doesn't lift the lock"
update.record("new", files=[])  # installing the update lifts it
assert update.required() is None
update.fetch_json, update.installed = real_fetch, real_installed
update.INSTALLED_FILE.unlink(missing_ok=True)

# A model name can't point outside the models folder (delete_model deletes that folder)
import models  # noqa: E402

assert models.cache_folder("small").parent == models.WHISPER_DIR
for bad in (r"..\..\Users", "../x", "a/../../b", "..", r"x\y"):
    try:
        models.cache_folder(bad)
        raise AssertionError(f"{bad!r} should be refused")
    except ValueError:
        pass
assert set(models.WHISPER_REVISIONS) == set(models.WHISPER_REPOS.values()), "every offered model is pinned"
assert all(len(v) == 40 for v in models.WHISPER_REVISIONS.values())
assert set(models.SHA256) == {models.SEG_URL, models.EMB_URL, models.VOICE_URL}

# A speaker model download that isn't the expected file is thrown away, never kept
import tempfile as _tf  # noqa: E402
import urllib.request as _ur  # noqa: E402

real_retrieve, real_models_dir, real_emb = _ur.urlretrieve, models.MODELS_DIR, models.EMB_MODEL
with _tf.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    models.MODELS_DIR = tmp
    (tmp / "sherpa-onnx-pyannote-segmentation-3-0").mkdir()
    (tmp / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx").write_bytes(b"x")
    real_seg, real_voice = models.SEG_MODEL, models.VOICE_MODEL
    models.SEG_MODEL = tmp / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
    models.EMB_MODEL, models.VOICE_MODEL = tmp / "emb.onnx", tmp / "voice.onnx"
    _ur.urlretrieve = lambda url, path, reporthook=None: Path(path).write_bytes(b"not the model")
    try:
        models.download_speakers()
        raise AssertionError("a wrong download should be refused")
    except OSError as e:
        assert "wasn't the file it should be" in str(e)
    assert not models.EMB_MODEL.exists() and not list(tmp.glob("*.part")), list(tmp.iterdir())
    _ur.urlretrieve, models.MODELS_DIR, models.EMB_MODEL = real_retrieve, real_models_dir, real_emb
    models.SEG_MODEL, models.VOICE_MODEL = real_seg, real_voice

# An update is all or nothing: a file that can't be replaced puts back the ones already done
with _tf.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    here, new = tmp / "here", tmp / "new"
    here.mkdir(); new.mkdir()
    for name in ("a.py", "b.py", "c.py"):
        (here / name).write_text("old " + name)
        (new / name).write_text("new " + name)
    real_here, real_replace = update.HERE, update.os.replace
    update.HERE = here
    assert update.inside("../outside.txt") is None and update.inside("a.py") == (here / "a.py").resolve()

    def flaky(src, dst):
        if Path(dst).name == "c.py" and str(src).endswith(update.NEW):  # the new c.py can't go in
            raise PermissionError(13, "in use")
        real_replace(src, dst)
    update.os.replace = flaky
    try:
        update.swap_in(new, ["a.py", "b.py", "c.py"])
        raise AssertionError("should have failed")
    except update.UpdateError as e:
        assert "put back" in str(e)
    update.os.replace = real_replace
    assert sorted(p.name for p in here.iterdir()) == ["a.py", "b.py", "c.py"], list(here.iterdir())
    assert all((here / n).read_text() == "old " + n for n in ("a.py", "b.py", "c.py"))
    try:
        update.swap_in(new, ["../evil.py"])
        raise AssertionError("a file outside the folder should be refused")
    except update.UpdateError:
        pass
    update.swap_in(new, ["a.py", "b.py", "c.py"])
    assert all((here / n).read_text() == "new " + n for n in ("a.py", "b.py", "c.py"))
    assert sorted(p.name for p in here.iterdir()) == ["a.py", "b.py", "c.py"]
    update.HERE = real_here

# The uninstall script: % in a name survives the batch file, and nothing outside the program folder is listed
import uninstall  # noqa: E402

with _tf.TemporaryDirectory() as tmp:
    plan = {"data": str(Path(tmp) / "data"), "program": str(Path(tmp) / "prog"), "shortcut": None,
            "program_files": ["app.py", "../../outside.txt", "ui/index.html"], "voices": ["100%.wav"],
            "voices_to": str(Path(tmp) / "clips")}
    text = uninstall.script(plan, True, 1234)
    assert "outside.txt" not in text and "app.py" in text and "index.html" in text
    if sys.platform == "win32":
        assert "100%%.wav" in text and "100%.wav" not in text.replace("100%%.wav", "")

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

# Progress goes window by window (as faster-whisper decodes them, silent ones too), not by the
# end of each line of text: it used to sit still through a window with no words, then jump
class WindowedModel:
    def transcribe(self, audio, language, **kw):
        import faster_whisper.transcribe as fw

        assert kw.get("log_progress"), "faster-whisper only counts windows when asked to"

        def gen():
            bar = fw.tqdm(total=90.0, unit="seconds", disable=False)  # what faster-whisper does
            # The middle window is silent; the first line claims to run to 89 s (Whisper does that on
            # music), which must not send the bar to the end
            for window, words in enumerate([[(1.0, 2.0), (2.5, 89.0)], [], [(70.0, 71.0)]]):
                for s, e in words:
                    yield Seg(s, e, "w")
                    assert progress.n <= 30.0 * window, ("went by a line's end time", progress.n)
                bar.update(30.0)
                seen.append(round(progress.n, 1))
            bar.close()
        return gen(), Info()


class Progress(Bar):
    total, n = 90.0, 0.0


seen, progress = [], Progress()
tokens, _ = ms.transcribe({"model": WindowedModel(), "device": "cpu"}, "base", np.zeros(90 * sr, dtype=np.float32), None, 5, progress)
assert seen == [30.0, 60.0, 90.0], seen
assert [t.word for t in tokens] == [" w", " w", " w"]
import faster_whisper.transcribe as fw  # noqa: E402

assert fw.tqdm.__module__.startswith("tqdm"), "faster-whisper's own progress bar is put back afterwards"

# The percentage is the whole video's: transcribing (with speaker detection, the slower of the two)
# is most of it, and it never says 100% before the subtitles are saved
steps = [ms.overall_progress("transcribe", 0.5, 0.9), ms.overall_progress("transcribe", 1.0, 0.6),
         ms.overall_progress("speakers", 1.0, 0.8), ms.overall_progress("voices", matched=0.0),
         ms.overall_progress("voices", matched=1.0), ms.overall_progress("lines"), ms.overall_progress("writing")]
assert steps == sorted(steps) and max(steps) < 1 and steps[1] == round(ms.TRANSCRIBED_AT * 0.6, 4), steps
assert ms.overall_progress("transcribe", 1.0) == ms.TRANSCRIBED_AT

# A window that stops answering mustn't stop the video: send() never waits for it, later events
# still reach the page, and only the newest progress is kept while it's busy
import threading  # noqa: E402
import time  # noqa: E402


class StuckWindow:
    def __init__(self):
        self.scripts, self.release = [], threading.Event()

    def evaluate_js(self, script):
        if not self.scripts:
            self.scripts.append(script)
            self.release.wait()  # the first call never comes back, as evaluate_js sometimes doesn't
            return
        self.scripts.append(script)

    def set_title(self, title):
        pass


mail_api = app.Api()
mail_api._outbox.WAIT = 0.3
mail_api._window = StuckWindow()
began = time.monotonic()
mail_api.send({"type": "log", "text": "first"})
for n in range(50):
    mail_api.send({"type": "progress", "done": n, "total": 0})
mail_api.send({"type": "done", "files": []})
assert time.monotonic() - began < 0.2, "send() waited for the window"
assert mail_api._outbox.flush(5)
sent = mail_api._window.scripts
assert '"first"' in sent[0] and '"done"' in sent[-1], sent
progress = [x for x in sent if '"progress"' in x]
assert progress and len(progress) < 50 and '"done": 49' in progress[-1], progress
assert mail_api._outbox.given_up == 1
mail_api._window.release.set()

# A video in Google Drive is copied here first (with a counter), read from the copy, and the copy goes
cloud_dir = Path(tempfile.mkdtemp())
ms.INCOMING = cloud_dir / "incoming"
video = cloud_dir / "talk.wav"
video.write_bytes(b"x" * (3 * ms.COPY_PIECE + 5))
real_place = ms.cloud_place
assert ms.local_copy(video) == video  # a local file is read where it is
ms.cloud_place = lambda path: "Google Drive"
copy = ms.local_copy(video)
assert copy.parent == ms.INCOMING and copy.read_bytes() == video.read_bytes()
ms.clear_incoming()
assert not ms.INCOMING.exists()
ms.cloud_place = real_place

# What's new: versions from CHANGELOG.md, shown once after an update, never on a new install
import changelog  # noqa: E402

listed = changelog.versions()
assert listed and listed[0]["version"] == changelog.current() and listed[0]["changes"], listed[:1]
assert all(v["changes"] for v in listed), "every version needs at least one change"
parsed = changelog.versions("## 1.2.0 · 1 May\n- One\n  more\n- Two\n\n## 1.1.0\n- Old\n")
assert parsed == [{"version": "1.2.0", "date": "1 May", "changes": ["One more", "Two"]},
                  {"version": "1.1.0", "date": None, "changes": ["Old"]}], parsed
changelog.SEEN_FILE = Path(tempfile.mkdtemp()) / "seen_version.txt"
assert changelog.news(first_run=True) == [] and changelog.SEEN_FILE.read_text() == changelog.current()
assert changelog.news() == []  # already seen
changelog.SEEN_FILE.unlink()
assert [v["version"] for v in changelog.news()] == [changelog.current()]  # updated from before 0.2.0
if len(listed) > 2:
    changelog.mark_seen(listed[2]["version"])
    assert [v["version"] for v in changelog.news()] == [listed[0]["version"], listed[1]["version"]]
changelog.mark_seen()
assert changelog.news() == []

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
