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

# People named in the check are remembered from the lines as checked: someone added in the check
# counts, a single "yeah" is too little to know a voice by, and a failure never stops the saving
class PrintsByLine:
    def __init__(self, fail=False):
        self.fail = fail
    def speaker_fingerprints(self, audio, turns):
        if self.fail:
            raise RuntimeError("model broke")
        return {spk: (np.full(4, np.nan) if spk == 3 else np.eye(4)[spk]) for _, _, spk in turns}

checked = [[0, 4, "Hi.", 0], [4, 8, "Hello.", 2], [8, 8.5, "Yeah.", 1], [9, 13, "Mm.", 3]]
ms.remember_people(PrintsByLine(), None, checked, {0: "Alex", 1: "Sam", 2: "Robin", 3: "Kai"})
assert sorted(r["name"] for r in known_voices.listing()) == ["Alex", "Robin"]  # Sam too short, Kai's print broken
known_voices.forget_all()
ms.remember_people(PrintsByLine(fail=True), None, checked, {0: "Alex"})
assert known_voices.listing() == []
known_voices.forget_all()

# The same after the whole queue: a person added in the check has no fingerprint from detection,
# so the voices come from the kept sound and the checked lines
import models  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    sound = Path(d) / "s.npy"
    np.save(sound, np.zeros(16000 * 14, dtype=np.int16))
    bundle = Path(d) / "b.json"
    bundle.write_text(__import__("json").dumps({
        "input": str(Path(d) / "v.mp4"), "out": str(Path(d) / "v.srt"), "style": "name", "sound": sound.name,
        "cues": [[0, 4, "Hi.", 0], [4, 8, "Hello.", 0]], "names": {"0": "Speaker 1"},
        "prints": {"0": [1.0, 0, 0, 0]}, "length": 14, "language": "en"}), encoding="utf-8")
    real = (ms.review, ms.save_outputs, speakers.SpeakerDetector, models.speakers_ready)
    ms.review = lambda cues, names, *_: ([[0, 4, "Hi.", 0], [4, 8, "Hello.", 1]], {0: "Alex", 1: "Robin"})
    ms.save_outputs = lambda *a: "saved"
    speakers.SpeakerDetector = lambda *a: PrintsByLine()
    models.speakers_ready = lambda: True
    try:
        args = type("A", (), {"finish": str(bundle), "settings": dict(paths.DEFAULT_SETTINGS, remember_voices=True)})()
        assert ms.finish_later(args) == "saved"
    finally:
        ms.review, ms.save_outputs, speakers.SpeakerDetector, models.speakers_ready = real
    assert sorted(r["name"] for r in known_voices.listing()) == ["Alex", "Robin"], known_voices.listing()
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
real_fetch, real_installed, real_recent = update.fetch_json, update.installed, update.recent_commits
# (These go through the API: the commits feed, tried first, isn't there)
update.recent_commits = lambda branch: (_ for _ in ()).throw(update.UpdateError("no feed"))
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

# What's new lists ten changes, and says how many more there are
replies["/compare/old...new"]["commits"] = [{"commit": {"message": f"Change {i}"}} for i in range(14)]
update.fetch_json = lambda path: replies[path]
update.installed = lambda: {"sha": "old", "date": None, "files": []}
status = update.check("main")
assert len(status["news"]) == 10 and status["more"] == 4, status
update.fetch_json, update.installed, update.recent_commits = real_fetch, real_installed, real_recent

# Usually updates come from the commits feed (no API limit, which a shared network runs out of):
# the version, its date, and what's new since the installed one, with each whole first line
# (the feed's titles are cut at 80 characters, so they come from the content)
SHA = {name: (name * 40)[:40] for name in "abc"}
feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>tag:github.com,2008:Grit::Commit/{SHA['c']}</id><title>Feature: A very long title that GitHub cuts…</title>
    <updated>2026-11-01T10:00:00Z</updated>
    <content type="html">&lt;pre&gt;Feature: A very long title that GitHub cuts short, but the content doesn&amp;#39;t

More words.&lt;/pre&gt;</content></entry>
  <entry><id>tag:github.com,2008:Grit::Commit/{SHA['b']}</id><title>Critical: Bad thing</title>
    <updated>2026-10-31T10:00:00Z</updated><content type="html">&lt;pre&gt;Critical: Bad thing&lt;/pre&gt;</content></entry>
  <entry><id>tag:github.com,2008:Grit::Commit/{SHA['a']}</id><title>Tidy</title>
    <updated>2026-10-30T10:00:00Z</updated><content type="html">&lt;pre&gt;Tidy&lt;/pre&gt;</content></entry>
</feed>""".encode()
asked = []
real_raw_fetch = update.fetch
update.fetch = lambda url, accept=None, progress=None: (asked.append(url), feed)[1]
update.fetch_json = lambda path: (_ for _ in ()).throw(AssertionError(f"the API shouldn't be needed: {path}"))
update.installed = lambda: {"sha": SHA["a"], "date": None, "files": []}
commits = update.recent_commits("main")
assert [c["sha"] for c in commits] == [SHA["c"], SHA["b"], SHA["a"]]
assert commits[0]["message"] == "Feature: A very long title that GitHub cuts short, but the content doesn't", commits[0]
status = update.check("main")
assert status["latest"]["sha"] == SHA["c"] and status["latest"]["date"] == "2026-11-01T10:00:00Z"
assert [n["kind"] for n in status["news"]] == ["critical", "feature"] and status["required"], status
assert all("api.github.com" not in u for u in asked), asked
update.record(SHA["c"], files=[])
# Installed further back than the feed goes, and no API: everything the feed has counts
update.fetch_json = lambda path: (_ for _ in ()).throw(update.UpdateError("rate limited"))
update.installed = lambda: {"sha": "0" * 40, "date": None, "files": []}
assert len(update.check("main")["news"]) == 3
# The download comes from codeload, and the API's copy only if that fails
asked.clear()
update.fetch = lambda url, accept=None, progress=None: (asked.append(url), (_ for _ in ()).throw(update.UpdateError("x")))[1]
try:
    update.apply({"sha": SHA["c"]})
except update.UpdateError:
    pass
assert "codeload.github.com" in asked[0] and "api.github.com" in asked[1], asked
update.fetch, update.fetch_json, update.installed = real_raw_fetch, real_fetch, real_installed
update.REQUIRED_FILE.unlink(missing_ok=True)
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

# A models folder (or one model) that's a link elsewhere still counts as inside the models folder
assert models.cache_folder("large-v3-turbo").name == "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
for bad in ("C:x", "a:b"):
    try:
        models.cache_folder(bad)
        raise AssertionError(f"{bad!r} should be refused")
    except ValueError:
        pass

# After an update the web view's saved copy of the page goes (it kept showing the 29 October page),
# and only then: an unchanged page keeps its caches, so opening stays quick
with tempfile.TemporaryDirectory() as tmp:
    storage = Path(tmp)
    cache = storage / "EBWebView" / "Default" / "Cache"
    cache.mkdir(parents=True)
    (cache / "old").write_text("old page")
    app.forget_old_page(storage)
    assert not cache.exists() and (storage / "page.txt").exists()
    cache.mkdir(parents=True)
    (cache / "new").write_text("new page")
    app.forget_old_page(storage)
    assert (cache / "new").exists(), "the same page again leaves the cache alone"
    (storage / "page.txt").write_text("a different page")
    app.forget_old_page(storage)
    assert not cache.exists()

# A proper Windows app: a copy that isn't installed (this one, in the tests) doesn't register,
# and Uninstall takes away the Start menu entry and Settings > Apps' entry
import uninstall  # noqa: E402
import winapp  # noqa: E402

if sys.platform == "win32":
    assert "not an installed copy" in winapp.ensure(), "only the installed copy registers"
    assert winapp.start_menu_link().name == "Subtitle Maker.lnk" and "Start Menu" in str(winapp.start_menu_link())
    removal = uninstall.script({"data": str(Path(tempfile.gettempdir()) / "x"), "program": None, "shortcut": None,
                                "program_files": [], "voices": [], "voices_to": ""}, True, 1)
    assert str(winapp.start_menu_link()) in removal and f'reg delete "HKCU\\{winapp.KEY}" /f' in removal, removal

# Languages: the window's list is exactly Whisper's, and a code it doesn't know is never saved
from faster_whisper.tokenizer import _LANGUAGE_CODES  # noqa: E402

assert set(paths.LANGUAGES) == set(_LANGUAGE_CODES), set(paths.LANGUAGES) ^ set(_LANGUAGE_CODES)
paths.save_settings({**paths.load_settings(), "language": "zz"})
assert paths.load_settings()["language"] == paths.DEFAULT_SETTINGS["language"], "an unknown code goes back to the default"
paths.save_settings({**paths.load_settings(), "language": "ta"})
assert paths.load_settings()["language"] == "ta"
paths.save_settings({**paths.load_settings(), "language": None})
assert paths.load_settings()["language"] is None, "Auto-detect stays Auto-detect"
saver = app.Api.__new__(app.Api)
assert saver.save_settings("high", "yue")["language"] == "yue"
assert saver.save_settings("high", "klingon")["language"] is None, "an unknown code becomes Auto-detect"
paths.save_settings(paths.DEFAULT_SETTINGS)

# Subtitles in English: off by default, only true/false, and the turbo model (which can't
# translate) hands over to one that can
assert paths.DEFAULT_SETTINGS["translate"] is False
paths.save_settings({**paths.load_settings(), "translate": "yes"})
assert paths.load_settings()["translate"] is False, "anything but true/false goes back to off"
assert paths.TRANSLATE_WITH["large-v3-turbo"] in {lv["model"] for lv in paths.LEVELS.values()}
assert "translate" in app.Api._INSTANT
paths.save_settings(paths.DEFAULT_SETTINGS)

# Remember where you scrolled: on by default, only true/false, saved from the window like the
# other switches, and Reset settings turns it back on
assert paths.DEFAULT_SETTINGS["remember_scroll"] is True
assert "remember_scroll" in app.Api._INSTANT
assert saver.save_prefs({"remember_scroll": False})["remember_scroll"] is False
assert paths.load_settings()["remember_scroll"] is False, "it's kept on disk"
assert saver.save_prefs({"remember_scroll": "sometimes"})["remember_scroll"] is True, "anything but true/false goes back to on"
saver.save_prefs({"remember_scroll": False})
assert saver.reset_prefs()["remember_scroll"] is True
paths.save_settings(paths.DEFAULT_SETTINGS)

# --- Settings > Hardware ---
import hardware  # noqa: E402
import winextras  # noqa: E402

# The default is Balanced: no caps, other apps first
assert hardware.preset_of(paths.DEFAULT_SETTINGS) == "share"
assert hardware.PRESETS["full"] == {"cpu_percent": 100, "low_priority": False, "memory_limit_gb": 0, "gpu_low_memory": False}
assert all(set(p) == set(hardware.KEYS) for p in hardware.PRESETS.values())
assert all(k in app.Api._INSTANT for k in hardware.KEYS)
# Only the offered values are kept; anything else goes back to the default
for key, bad in (("cpu_percent", 33), ("cpu_percent", "50"), ("low_priority", "yes"), ("memory_limit_gb", 5),
                 ("memory_limit_gb", -1), ("gpu_low_memory", 1.5)):
    assert saver.save_prefs({key: bad})[key] == paths.DEFAULT_SETTINGS[key], (key, bad)
assert saver.save_prefs({"cpu_percent": 50})["cpu_percent"] == 50
assert paths.load_settings()["cpu_percent"] == 50, "it's kept on disk"
assert hardware.preset_of(paths.load_settings()) == "custom"
# Picking a preset sets all it covers; a setting sent with it wins
light = saver.save_prefs({"hw_preset": "light"})
assert hardware.preset_of(light) == "light" and light["cpu_percent"] == 25 and light["gpu_low_memory"] is True
assert saver.save_prefs({"hw_preset": "nonsense"})["cpu_percent"] == 25, "an unknown preset changes nothing"
assert saver.save_prefs({"hw_preset": "full", "memory_limit_gb": 8})["memory_limit_gb"] == 8
assert saver.reset_prefs()["cpu_percent"] == 100 and hardware.preset_of(paths.load_settings()) == "share"
paths.save_settings(paths.DEFAULT_SETTINGS)

# Threads: a share of the processor's threads, at least 1, set only when that's fewer than the 4
# the models use anyway (the cap holds the rest); none under no limit, as in 0.3.0
assert hardware.threads_for(100, 12) is None
assert hardware.threads_for(25, 12) == 3 and hardware.threads_for(25, 2) == 1
assert hardware.threads_for(50, 12) is None and hardware.threads_for(75, 12) is None, "the cap alone"
assert hardware.threads_for(25, 4) == 1 and hardware.threads_for(75, 4) == 3

# The worker's environment: thread variables only under a processor limit, and nothing left over
# from the app's own environment
base = {"PATH": "x", "OMP_NUM_THREADS": "9", hardware.ENV_MEMORY: "1"}
env = hardware.worker_env(paths.DEFAULT_SETTINGS, base, cores=12)
assert env == {"PATH": "x", "OMP_NUM_THREADS": "9"}, env
capped = {**paths.DEFAULT_SETTINGS, "cpu_percent": 25, "memory_limit_gb": 4, "gpu_low_memory": True}
env = hardware.worker_env(capped, {"PATH": "x"}, cores=12)
assert all(env[v] == "3" for v in hardware.THREAD_VARS), env
assert env[hardware.ENV_THREADS] == "3" and env[hardware.ENV_CPU] == "25" and env[hardware.ENV_MEMORY] == "4096"
assert env[hardware.ENV_GPU_LOW] == "1" and base["PATH"] == "x"
assert hardware.env_int(hardware.ENV_THREADS, env) == 3 and hardware.env_int(hardware.ENV_THREADS, {}) is None
assert hardware.env_int(hardware.ENV_CPU, {hardware.ENV_CPU: "lots"}) is None
assert hardware.limits(capped, cores=12) == {"threads": 3, "cpu_percent": 25, "memory_mb": 4096,
                                             "low_priority": True, "gpu_low_memory": True, "device": "auto"}
assert hardware.limits({**paths.DEFAULT_SETTINGS, "device": "cpu"}, 12) != hardware.limits(paths.DEFAULT_SETTINGS, 12), \
    "switching to the processor starts a fresh worker (which lets go of the graphics memory)"
# Light on a card where "Use less graphics memory" changes nothing is still Light, either way
light_off = {**paths.DEFAULT_SETTINGS, **hardware.PRESETS["light"], "gpu_low_memory": False}
assert hardware.preset_of(light_off) == "custom" and hardware.preset_of(light_off, gpu_low_memory=False) == "light"
assert hardware.limits(paths.DEFAULT_SETTINGS, 12) != hardware.limits(capped, 12), "a change starts a fresh worker"
half = hardware.worker_env({**paths.DEFAULT_SETTINGS, "cpu_percent": 50}, {"PATH": "x"}, cores=12)
assert half == {"PATH": "x", hardware.ENV_CPU: "50"}, "50% of 12 threads: the cap, threads left alone"
# The worker and the speaker step read the limit; WhisperModel gets it, and nothing without one
_saved_env = dict(app.os.environ)
app.os.environ.update({hardware.ENV_THREADS: "3"})
assert ms.cpu_limits() == {"cpu_threads": 3, "num_workers": 1}
app.os.environ.pop(hardware.ENV_THREADS)
assert ms.cpu_limits() == {}
app.os.environ.clear()
app.os.environ.update(_saved_env)
# Remembered speeds are kept apart under a processor limit
assert hardware.speed_suffix({hardware.ENV_CPU: "25"}) == "|cpu25" and hardware.speed_suffix({}) == ""

# The graphics card's format: the fastest, or the smallest that's still fast; the same on a card
# without float16 (GTX 10-series), where "Use less graphics memory" isn't offered
assert hardware.compute_type({"float16", "int8_float16", "int8", "float32"}) == "float16"
assert hardware.compute_type({"float16", "int8_float16", "int8", "float32"}, low_memory=True) == "int8_float16"
assert hardware.compute_type({"int8", "int8_float32", "float32"}, True) == hardware.compute_type({"int8", "int8_float32", "float32"}) == "int8"
assert hardware.saves_gpu_memory({"float16", "int8_float16", "int8"}) and not hardware.saves_gpu_memory({"int8", "float32"})

# The memory pre-flight: the level (on the processor or the graphics card), speakers, the sound
gtx = {"gpu": "GTX 1050", "vram_mb": 3072}
assert all(set(lv["ram_mb"]) == {"cpu", "cuda"} and lv["ram_mb"]["cpu"] >= lv["ram_mb"]["cuda"] for lv in paths.LEVELS.values())
assert hardware.device_for(paths.DEFAULT_SETTINGS, gtx, "high") == "cuda"
assert hardware.device_for(paths.DEFAULT_SETTINGS, {"gpu": "x", "vram_mb": 2048}, "max") == "cpu", "Automatic: too big"
assert hardware.device_for(paths.DEFAULT_SETTINGS, {"gpu": None}, "fast") == "cpu"
assert hardware.device_for({**paths.DEFAULT_SETTINGS, "device": "cpu"}, gtx, "fast") == "cpu"
need = hardware.memory_needed_mb("high", "cpu", speakers=True, minutes=10)
assert need == paths.LEVELS["high"]["ram_mb"]["cpu"] + paths.SPEAKERS_RAM_MB + 10 * paths.AUDIO_RAM_MB_PER_MINUTE
assert hardware.memory_needed_mb("potato", "cpu", speakers=True, minutes=0) == paths.LEVELS["potato"]["ram_mb"]["cpu"], \
    "Potato never detects speakers"
assert hardware.memory_check(paths.DEFAULT_SETTINGS, gtx)["fits"], "no limit: always fits"
tight = {**paths.DEFAULT_SETTINGS, "memory_limit_gb": 2}
check = hardware.memory_check(tight, gtx, speakers=True)
assert not check["fits"] and check["limit_mb"] == 2048 and check["need_mb"] > 2048
assert hardware.memory_check({**tight, "level": "potato"}, gtx, minutes=10)["fits"]
assert hardware.memory_check({**paths.DEFAULT_SETTINGS, "memory_limit_gb": 8}, gtx, speakers=True)["fits"]
# A little over by the (rounded-up) estimate isn't warned about: High with speaker names on the
# graphics card is estimated at 4.1 GB, and a 4 GB limit lets it through
four = hardware.memory_check({**paths.DEFAULT_SETTINGS, "memory_limit_gb": 4}, gtx, speakers=True)
assert four["need_mb"] > 4096 and four["fits"]
assert not hardware.memory_check({**paths.DEFAULT_SETTINGS, "memory_limit_gb": 3}, gtx, speakers=True)["fits"]

# Running out of memory is said plainly: over the limit in Settings, or the whole PC
assert hardware.is_memory_error(MemoryError())
assert hardware.is_memory_error("RuntimeError: std::bad_alloc")
assert hardware.is_memory_error("RuntimeException: [ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : Non-zero status code "
                                "returned while running Pad node. Status Message: bad allocation"), "the speech detector's"
assert hardware.is_memory_error("numpy._core._exceptions._ArrayMemoryError: Unable to allocate 1.2 GiB")
assert hardware.is_memory_error("Speaker detection stopped (code 1): MemoryError")
assert hardware.is_memory_error(None, code=0xC0000017) and hardware.is_memory_error(None, code=-1073741801)
assert not hardware.is_memory_error("RuntimeError: CUDA failed with error out of memory"), "the graphics card's isn't"
assert not hardware.is_memory_error("FileNotFoundError: x.mp4") and not hardware.is_memory_error(None, code=1)
assert hardware.memory_message("MemoryError", limit_mb=2048) == hardware.MEMORY_MESSAGE
assert hardware.memory_message("It stopped unexpectedly (code 3221225477).", 2048, hit=True) == hardware.MEMORY_MESSAGE
assert "Settings > Hardware" in hardware.MEMORY_MESSAGE
assert hardware.memory_message("MemoryError") == hardware.OUT_OF_MEMORY, "no limit set: the PC itself ran out"
assert hardware.memory_message("Couldn't read the sound", 2048) == "Couldn't read the sound"
assert hardware.memory_message("Couldn't read the sound", 2048, code=1) == "Couldn't read the sound"

# The app's side: a worker's error over its limit becomes the plain message, flagged as memory
class _FakeJob:
    handle, hits = 1, [True]
    def new_hits(self):  # noqa: E301
        return self.hits.pop(0) if self.hits else False
class _FakeWorker:  # noqa: E302
    limits, job = hardware.limits(tight), _FakeJob()
worker = _FakeWorker()
assert saver._plain_error(worker, "It stopped unexpectedly (code 1).") == (hardware.MEMORY_MESSAGE, True)
assert worker.restart, "the next video starts a fresh worker"
# Going over in an earlier video doesn't count for the next one (each video starts afresh)
worker = _FakeWorker()
worker.job.hits = [True]
saver._memory_hit(worker, fresh=True)
assert saver._plain_error(worker, "Something went wrong: ValueError: x") == ("Something went wrong: ValueError: x", False)
assert not getattr(worker, "restart", False), "a video that went fine doesn't restart the worker"
_FakeWorker.job = None
assert saver._plain_error(_FakeWorker(), "Couldn't read the sound") == ("Couldn't read the sound", False)

# The Job Object itself (Windows): the cap and the limit read back as set, and going over the
# limit is noticed
if winextras.WINDOWS:
    import subprocess as _sp  # noqa: E402

    child = _sp.Popen([sys.executable, "-c", "import time; time.sleep(0.3); x = bytearray(600 * 2**20)"],
                      stderr=_sp.PIPE)
    job, problem = winextras.limit_job(child, cpu_percent=25, memory_mb=200)
    assert job and not problem, problem
    assert job.cpu_rate() == 25 and job.memory_limit_mb() == 200
    child.wait()
    assert child.returncode != 0 and b"MemoryError" in child.stderr.read()
    assert job.new_hits(), "Windows said it went over"
    assert not job.new_hits(), "and only once"
    job.close()
    assert winextras.limit_job(child) == (None, None), "nothing to limit: no job"
    # nvidia-smi (here: another Python) started with CREATE_BREAKAWAY_FROM_JOB leaves the job, so
    # its memory isn't counted; and closing the job ends what's still in it
    # (the plain interpreter, not the venv's launcher, which starts a second process; started
    # suspended until it's in the job, as app.py does)
    base_python = getattr(sys, "_base_executable", sys.executable)
    child = _sp.Popen([base_python, "-c", "import subprocess, sys, time; subprocess.run([sys.executable, '-c', "
                       "'pass'], creationflags=0x01000000); print('ran', flush=True); time.sleep(60)"],
                      stdout=_sp.PIPE, text=True, creationflags=0x4)
    job, problem = winextras.limit_job(child, memory_mb=4000)
    import psutil as _ps  # noqa: E402

    _ps.Process(child.pid).resume()
    assert child.stdout.readline().strip() == "ran"
    assert job.processes() == 1, f"the breakaway child stayed out of the job ({job.processes()} in it)"
    job.close()
    child.wait(timeout=10)
    assert child.returncode is not None, "closing the job ended the process in it"

# The meters in Settings > Quality: every level says how fast and how accurate it is, out of 5
assert all(1 <= lv[k] <= 5 for lv in paths.LEVELS.values() for k in ("speed", "accuracy"))

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
        assert "put back" in str(e) and "c.py" in str(e), e
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
    assert opened["formatted"], "the <i> is formatting the editor can't keep, so it says so"
    plain, positioned = d / "plain.srt", d / "positioned.srt"
    plain.write_text("1\n00:00:01,000 --> 00:00:02,000\nA {curly} word\n", encoding="utf-8")
    positioned.write_text("1\n00:00:01,000 --> 00:00:02,000\n{\\an8}On top\n", encoding="utf-8")
    found = {}
    assert srtfile.read_srt(plain, found)[0]["text"] == "A {curly} word" and not found
    found = {}
    assert srtfile.read_srt(positioned, found)[0]["text"] == "On top" and found["formatted"]
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
import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402

said = io.StringIO()
ms.EVENTS = True
with contextlib.redirect_stdout(said):
    copy = ms.local_copy(video)
ms.EVENTS = False
assert copy.parent == ms.INCOMING and copy.read_bytes() == video.read_bytes()
# Each piece says how far the copy has got and how big it is: the window's bar fills from that
copying = [json.loads(line[len(ms.EVENT_PREFIX):]) for line in said.getvalue().splitlines() if line.startswith(ms.EVENT_PREFIX)]
copying = [e for e in copying if e["type"] == "download"]
assert len(copying) == 4 and all(e["item"] == "video" and e["total"] for e in copying), copying
assert [e["done"] for e in copying] == sorted(e["done"] for e in copying) and copying[-1]["done"] == copying[-1]["total"]
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
# Versions are compared as numbers (0.x to 1.0.0 was a jump): someone two versions behind sees
# both, one behind only the newest, and 0.10 comes after 0.9
changelog.mark_seen(listed[2]["version"])
assert [v["version"] for v in changelog.news()] == [listed[0]["version"], listed[1]["version"]]
changelog.mark_seen(listed[1]["version"])
assert [v["version"] for v in changelog.news()] == [listed[0]["version"]]
assert changelog._key("0.10.0") > changelog._key("0.9.9") and changelog._key("1.0.0") > changelog._key("0.99.0")
changelog.mark_seen()
# The update notice comes from commit prefixes: a release's Feature: commits say "New features"
assert update.change("Feature: Hardware limits")["kind"] == "feature"
assert update.change("Improvement: Clearer grey text")["kind"] == "improvement"

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
# --- The page's own title bar (winextras.custom_frame), on a hidden window of this thread ---
if winextras.WINDOWS:
    import ctypes  # noqa: E402
    from ctypes import wintypes  # noqa: E402

    user32 = ctypes.WinDLL("user32")
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID)
    user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.SendMessageW.restype = ctypes.c_ssize_t
    for name in ("GetClientRect", "GetWindowRect"):
        getattr(user32, name).argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.DestroyWindow.argtypes = (wintypes.HWND,)

    def made(style):
        hwnd = user32.CreateWindowExW(0, "STATIC", "test", style, 100, 100, 640, 480, None, None, None, None)
        assert hwnd, "couldn't make a test window"
        return hwnd

    def client_height(hwnd):
        r = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(r))
        return r.bottom

    hwnd = made(0x00CF0000)  # WS_OVERLAPPEDWINDOW, hidden
    framed = client_height(hwnd)
    assert winextras.custom_frame(hwnd) is None
    # the caption's height goes to the page; the sides and bottom keep Windows' (invisible) sizing border
    outer = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    assert framed < client_height(hwnd) < outer.bottom - outer.top, (framed, client_height(hwnd))
    # a bare Alt doesn't send the window into a menu mode nobody can see; Alt+Space still opens the menu
    assert user32.SendMessageW(hwnd, winextras.WM_SYSCOMMAND, winextras.SC_KEYMENU, 0) == 0
    user32.DestroyWindow(hwnd)  # takes the subclass off as it goes (WM_NCDESTROY)
    popup = made(0x80000000)  # WS_POPUP: no frame to keep, so it's left alone
    assert winextras.custom_frame(popup), "a window without a frame and caption is left as it is"
    user32.DestroyWindow(popup)

    # Placement only comes back if the strip the window is dragged by is on a screen: the page's
    # strip is narrower (its buttons are at the right), so a window almost off the left edge whose
    # native title bar would still show a little is put somewhere sensible instead
    probe = made(0x00CF0000)
    nearly_off = {"rect": [-1000, 100, 60, 700], "maximized": False}
    assert winextras.set_placement(probe, nearly_off)
    assert not winextras.set_placement(probe, nearly_off, grip=(48, 186, 32))
    assert not winextras.set_placement(probe, {"rect": [-30000, -30000, -29000, -29300], "maximized": False})
    user32.DestroyWindow(probe)

# --- The installer scripts are plain ASCII ---
# Windows PowerShell 5.1 reads a .ps1 without a byte-order mark as ANSI, so one "…" or "█" in
# install.ps1 breaks Subtitle-Maker-Setup.exe, which runs it from a file (a paste is fine).
for script in ("install.ps1", "windows-app.ps1", "Setup.bat", "installer/SubtitleMaker.iss"):
    text = (ROOT / script).read_bytes()
    bad = [i for i, b in enumerate(text) if b > 127]
    assert not bad, f"{script} has non-ASCII characters (first at byte {bad[0]})"

# --- The GitHub workflows parse ---
# A workflow GitHub can't read fails on every push, under its file name, without running anything
import yaml  # noqa: E402  (ctranslate2 brings it)

for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
    parsed = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict) and parsed.get("jobs"), f"{workflow.name} has no jobs"

print(f"Unit tests passed (page API: {len(exposed)} functions)")
