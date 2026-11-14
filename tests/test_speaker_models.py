"""speaker_models.py against sherpa-onnx itself: the same voice fingerprints and the same turns.

Subtitle Maker used sherpa-onnx 2.0.1 for its speaker models until its unsigned DLLs turned out
to be blocked by Smart App Control; speaker_models.py does the same with onnxruntime. People's
remembered voices (known_voices.py) are fingerprints sherpa-onnx made, so the new ones must
match them. This compares the two on real speech.

It needs sherpa-onnx (pip install sherpa-onnx==2.0.1) and the speaker models already downloaded,
and skips (passing) without them, as on CI. Run with the program's Python:

    python tests/test_speaker_models.py [more audio files...]

The extra files (any format ffmpeg/PyAV reads) are added to the clips compared; a few minutes
of conversation is a good test. With SUBTITLE_MAKER_HOME on a scratch folder, SPEAKER_MODELS_DIR
can point at a models folder that has them (they're only read). sherpa-onnx runs in a process of its own: it brings its own
onnxruntime.dll, and two in one process would quietly share one.
"""
import importlib.util
import itertools
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
THREADS = 4
MIN_COSINE = 0.999       # fingerprints: sherpa's and ours, for the same audio
MIN_AGREEMENT = 0.95     # turns: speaking time given the same speaker (after matching up the numbers)


def models():
    """The speaker models: in the data folder's models, or (only read) in SPEAKER_MODELS_DIR's."""
    from models import EMB_MODEL, MODELS_DIR, SEG_MODEL, VOICE_MODEL
    folder = Path(os.environ.get("SPEAKER_MODELS_DIR") or MODELS_DIR)
    return {k: folder / p.relative_to(MODELS_DIR) for k, p in {"voice": VOICE_MODEL, "emb": EMB_MODEL, "seg": SEG_MODEL}.items()}


def clips(extra):
    """{name: 16 kHz float32 audio}: the repo's JFK clip, any given files, pieces of them, and
    a conversation of made-up voices (the JFK clip slowed down and sped up, with its own voice)."""
    from faster_whisper import decode_audio

    found = {"jfk": decode_audio(str(ROOT / "tests" / "media" / "jfk.flac"), sampling_rate=16000)}
    for path in extra:
        found[Path(path).stem] = decode_audio(str(path), sampling_rate=16000)
    jfk = found["jfk"]

    def stretch(a, rate):  # a lower or higher voice (crude resampling: a different "person")
        return np.interp(np.arange(0, len(a), rate), np.arange(len(a)), a).astype(np.float32)

    parts = []
    for k in range(3):
        for rate in (1.0, 0.8, 1.25):
            piece = stretch(jfk, rate)
            parts += [piece[k * len(piece) // 3:(k + 1) * len(piece) // 3], np.zeros(6400, np.float32)]
    found["voices"] = np.concatenate(parts)
    return {k: np.ascontiguousarray(v, dtype=np.float32) for k, v in found.items()}


def pieces(audio):
    """Fingerprint test pieces: lengths like a short sentence up to a voice clip (TitaNet tops
    out at about 2 minutes of audio in one go, in sherpa-onnx too)."""
    out = {}
    for name, a in audio.items():
        for seconds in (0.5, 1.5, 4, 12, 30, 100):
            n = int(seconds * 16000)
            if len(a) >= n:
                start = (len(a) - n) // 3
                out[f"{name}[{seconds}s]"] = a[start:start + n]
    return out


def run_sherpa(folder):
    import sherpa_onnx

    m = models()
    audio = {p.stem: np.load(p) for p in sorted(folder.glob("*.npy"))}
    result = {"voice": {}, "emb": {}, "turns": {}, "time": {}}
    for key in ("voice", "emb"):
        extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(m[key]), num_threads=THREADS))
        start = time.perf_counter()
        for name, a in pieces(audio).items():
            stream = extractor.create_stream()
            stream.accept_waveform(sample_rate=16000, waveform=a)
            stream.input_finished()
            result[key][name] = list(map(float, extractor.compute(stream)))
        result["time"][key] = time.perf_counter() - start
    for name, a in audio.items():
        for count in (-1, 2, 3):
            config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(m["seg"])),
                    num_threads=THREADS),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(m["emb"]), num_threads=THREADS),
                clustering=sherpa_onnx.FastClusteringConfig(num_clusters=count, threshold=0.5))
            diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
            start = time.perf_counter()
            turns = diarizer.process(a).sort_by_start_time()
            result["time"][f"{name}/{count}"] = time.perf_counter() - start
            result["turns"][f"{name}/{count}"] = [(t.start, t.end, t.speaker) for t in turns]
    return result


def run_ours(folder):
    import speaker_models

    m = models()
    audio = {p.stem: np.load(p) for p in sorted(folder.glob("*.npy"))}
    result = {"voice": {}, "emb": {}, "turns": {}, "time": {}}
    for key in ("voice", "emb"):
        embedder = speaker_models.Embedder(m[key], THREADS)
        start = time.perf_counter()
        for name, a in pieces(audio).items():
            result[key][name] = list(map(float, embedder.compute(a)))
        result["time"][key] = time.perf_counter() - start
    for name, a in audio.items():
        for count in (-1, 2, 3):
            diarizer = speaker_models.Diarizer(m["seg"], m["emb"], THREADS, num_clusters=count, threshold=0.5)
            start = time.perf_counter()
            result["turns"][f"{name}/{count}"] = diarizer.process(a)
            result["time"][f"{name}/{count}"] = time.perf_counter() - start
    return result


def agreement(a, b, step=0.01):
    """The share of speaking time both give the same speaker, with the speaker numbers matched
    up the best way (every pairing tried), and the number of speakers each found."""
    end = max([t[1] for t in a + b] + [step])
    ids_a, ids_b = sorted({t[2] for t in a}), sorted({t[2] for t in b})
    k = max(len(ids_a), len(ids_b), 1)

    def grid(turns, ids):
        g = np.zeros((k, int(end / step) + 2), bool)
        for s, e, spk in turns:
            g[ids.index(spk), int(round(s / step)):int(round(e / step))] = True
        return g

    ga, gb = grid(a, ids_a), grid(b, ids_b)
    best = max(sum((ga[i] & gb[p[i]]).sum() for i in range(k)) for p in itertools.permutations(range(k)))
    talk = (ga.sum() + gb.sum()) / 2
    return (best / talk if talk else 1.0), len(ids_a), len(ids_b)


def main():
    if sys.argv[1:2] == ["--sherpa"]:  # the child process: sherpa-onnx only
        json.dump(run_sherpa(Path(sys.argv[2])), open(sys.argv[3], "w"))
        return
    if importlib.util.find_spec("sherpa_onnx") is None:
        print("Skipped: sherpa-onnx isn't installed (pip install sherpa-onnx==2.0.1 to compare)")
        return
    if not all(p.exists() for p in models().values()):
        print("Skipped: the speaker models aren't downloaded")
        return
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for name, a in clips(sys.argv[1:]).items():
            np.save(folder / f"{name}.npy", a)
        child = subprocess.run([sys.executable, __file__, "--sherpa", str(folder), str(folder / "sherpa.json")],
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        assert child.returncode == 0, "sherpa-onnx's run failed"
        theirs = json.load(open(folder / "sherpa.json"))
        ours = run_ours(folder)
    failures = []
    for key in ("voice", "emb"):
        for name in theirs[key]:
            x, y = np.array(theirs[key][name]), np.array(ours[key][name])
            cos = float(x @ y / np.linalg.norm(x) / np.linalg.norm(y))
            print(f"fingerprint {key:5s} {name:24s} cosine {cos:.9f}")
            if cos < MIN_COSINE:
                failures.append(f"{key} {name}: cosine {cos:.6f}")
    for name in theirs["turns"]:
        a, b = theirs["turns"][name], ours["turns"][name]
        share, n_a, n_b = agreement(a, b)
        same = len(a) == len(b) and all(abs(p[0] - q[0]) < 1e-4 and abs(p[1] - q[1]) < 1e-4 and p[2] == q[2]
                                        for p, q in zip(a, b))
        print(f"turns {name:16s} speakers {n_a} / {n_b}  turns {len(a)} / {len(b)}  agreement {share:.4f}"
              f"{'  (identical)' if same else ''}")
        if n_a != n_b or share < MIN_AGREEMENT:
            failures.append(f"turns {name}: {n_a} vs {n_b} speakers, agreement {share:.4f}")
    for name in theirs["time"]:
        print(f"time {name:16s} sherpa-onnx {theirs['time'][name]:7.2f} s   onnxruntime {ours['time'][name]:7.2f} s")
    assert not failures, "\n".join(failures)
    print("The speaker models match sherpa-onnx")


if __name__ == "__main__":
    main()
