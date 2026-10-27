r"""Turn speech in a video or audio file into a movie-style .srt subtitle file.

Usage:
    python make_subtitles.py "path\to\clip.mp4"
    python make_subtitles.py "path\to\clip.mp4" --level fast --language en
    python make_subtitles.py "path\to\clip.mp4" --speakers 2

The quality level and language default to what you picked in Setup (or Settings.bat).

The .srt is written next to the input file. In DaVinci Resolve:
File > Import > Subtitle, then drag it onto a subtitle track.

With --speakers, it also works out who is talking. Put a 10-20 second clip of
each person talking alone in the Voices folder, named after them (Alex.wav),
and their names are filled in automatically; otherwise it plays you a clip of
each voice and asks. It then writes one .srt with "Name:" labels plus one .srt
per person.
"""

import argparse
import datetime
import base64
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import traceback
import wave
from collections import namedtuple
from pathlib import Path

import numpy as np
import psutil
from tqdm import tqdm


import models  # noqa: E402
from paths import ERROR_LOG, LEVELS, MODELS_DIR, add_history, level_for_model, load_settings  # noqa: E402


def add_cuda_dlls():
    """Let CTranslate2 find the cuBLAS/cuDNN DLLs from the nvidia-*-cu12 pip packages (Windows only)."""
    if sys.platform != "win32":
        return
    try:
        import nvidia
    except ImportError:
        return
    for base in nvidia.__path__:
        for bin_dir in Path(base).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]


add_cuda_dlls()

import ctranslate2  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402
from faster_whisper.audio import decode_audio  # noqa: E402

VOICES_DIR = Path(__file__).parent / "Voices"
SAMPLE_RATE = 16000

# A transcribed word, plus who said it (None when speakers aren't being detected)
Token = namedtuple("Token", "word start end speaker")

# Movie subtitle conventions
MAX_LINE_CHARS = 42
MAX_LINES = 2
MAX_CUE_SECONDS = 6.0
MIN_CUE_SECONDS = 1.0
PAUSE_SPLIT_SECONDS = 0.7   # a pause this long starts a new subtitle
GAP_BETWEEN_CUES = 0.08     # keep cues from touching so they don't flicker

VOICE_CLIP_SECONDS = 7.0    # how much of a speaker to play when asking who they are

# Beeps, window title and opening the folder only make sense in a console window someone is watching
IN_CONSOLE = sys.stdout.isatty()

# Set by --events: the app window runs this script and reads progress as JSON lines from stdout
EVENTS = False
EVENT_PREFIX = "@@"


def emit(kind, **data):
    """Send one event to the app window (only in --events mode)."""
    if EVENTS:
        print(EVENT_PREFIX + json.dumps({"type": kind, **data}), flush=True)


class EventBar:
    """Stands in for the tqdm bar in --events mode, sending progress to the app window instead."""

    def __init__(self, total, desc=""):
        self.total, self.n, self.desc, self.extra = total, 0.0, desc.strip(), None

    def update(self, amount):
        self.n += amount
        self.send()

    def set_description_str(self, desc):
        self.desc = desc.strip()
        self.send()

    def set_postfix_str(self, status):
        pass  # the window gets structured "resources" events instead

    def send(self):
        emit("progress", done=round(self.n, 1), total=self.total, stage=self.desc,
             speakers=self.extra() if self.extra else None)

    def write(self, message):
        print(message, flush=True)

    def close(self):
        pass


class FriendlyError(Exception):
    """An error with a message meant for the user; the traceback still goes to ERROR_LOG."""


def gpu_mb_needed(model_name):
    """Rough extra GPU memory the model needs (int8), in MB, from the measured LEVELS table."""
    level = level_for_model(model_name)
    return level["vram_mb"] if level else 1400


def gpu_stats():
    """(utilisation %, used MB, total MB) from nvidia-smi, or None if unavailable."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        util, used, total = (int(x) for x in out.splitlines()[0].split(","))
        return util, used, total
    except Exception:
        return None


def resource_stats(gpu=True):
    """CPU, RAM and (if there is an NVIDIA GPU) GPU use right now, RAM and VRAM in MB.

    gpu=False skips asking nvidia-smi, the one slow part (Potato mode).
    """
    ram = psutil.virtual_memory()
    stats = {"cpu": psutil.cpu_percent(), "ram_used": ram.used / 2**20, "ram_total": ram.total / 2**20}
    gpu = gpu_stats() if gpu else None
    if gpu:
        stats.update(gpu=gpu[0], vram_used=gpu[1], vram_total=gpu[2])
    return stats


def resource_line(stats=None):
    s = stats or resource_stats()
    parts = [f"CPU {s['cpu']:.0f}%", f"RAM {s['ram_used'] / 1024:.1f}/{s['ram_total'] / 1024:.0f}GB"]
    if "gpu" in s:
        parts.insert(0, f"GPU {s['gpu']}% VRAM {s['vram_used'] / 1024:.1f}/{s['vram_total'] / 1024:.1f}GB")
    return "  ".join(parts)


class ResourceMonitor:
    """Refreshes the resource readout on the progress bar every second and tracks peaks."""

    def __init__(self, bar, extra=None, gpu=True):
        self.bar = bar
        self.extra = extra  # optional callable adding more status, e.g. speaker detection progress
        self.gpu = gpu  # False: CPU and RAM only, no nvidia-smi polling (Potato mode)
        self.stop_event = threading.Event()
        self.peak_vram = self.peak_ram = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        # Sample straight away, so even a run shorter than a second reports its peaks
        while True:
            stats = resource_stats(self.gpu)
            self.peak_vram = max(self.peak_vram, stats.get("vram_used", 0))
            self.peak_ram = max(self.peak_ram, stats["ram_used"] * 2**20)
            if EVENTS:
                emit("resources", **stats)
                self.bar.send()  # also refreshes speaker detection progress between segments
            status = resource_line(stats)
            if self.extra:
                status = f"{self.extra()}  {status}"
            self.bar.set_postfix_str(status)
            if self.stop_event.wait(1):
                break

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop_event.set()
        self.thread.join()


def preflight(model_name, device):
    """Print what the machine has free, and decide whether the GPU has room for the model."""
    print(f"Resources: {resource_line()}")
    if device == "cpu":
        return "cpu"
    gpu = gpu_stats()
    need = gpu_mb_needed(model_name)
    if gpu and gpu[2] - gpu[1] < need:
        msg = f"Only {(gpu[2] - gpu[1]) / 1024:.1f}GB VRAM free, '{model_name}' needs about {need / 1024:.1f}GB."
        if device == "cuda":
            print(f"Warning: {msg} Close games or other GPU apps if it fails.")
        else:
            print(f"{msg} Using CPU instead. Close GPU-heavy apps to use the GPU.")
            return "cpu"
    return device


def download_progress(item, label):
    """A progress(done_mb, total_mb) callback: events for the window, a bar in a console."""
    bar = {}

    def progress(done, total):
        if EVENTS:
            emit("download", item=item, label=label, done=round(done, 1), total=round(total, 1) if total else None)
            return
        if "bar" not in bar:
            bar["bar"] = tqdm(total=round(total) if total else None, unit="MB", desc=f"Downloading {label} ",
                              bar_format="{desc}{percentage:3.0f}%|{bar}| {n:.0f}/{total:.0f} MB [{remaining}]"
                              if total else "{desc}{n:.0f} MB")
        bar["bar"].update(min(done, bar["bar"].total or done) - bar["bar"].n)
        if total and done >= total:
            bar["bar"].close()
    return progress


def whisper_source(name):
    """The model's folder on this PC, downloading it first if it isn't here yet.

    Loading from the folder means a run never checks the internet once the model is downloaded.
    """
    path = models.whisper_path(name)
    if path:
        return str(path)
    level = level_for_model(name)
    size = f", {level['download']}" if level else ""
    print(f"Downloading the '{name}' speech model (first time only{size})...")
    return str(models.download_whisper(name, download_progress("whisper", f"the '{name}' speech model")))


def load_model(name, device):
    """Returns (model, device actually used)."""
    name = whisper_source(name)
    if device == "auto" and not shutil.which("nvidia-smi"):
        # No NVIDIA card (or no driver): trying CUDA first would only waste a few seconds
        print("No NVIDIA graphics card found, using the CPU.")
        device = "cpu"
    if device in ("auto", "cuda"):
        try:
            # Older GPUs (e.g. GTX 10-series) have no fast float16, so pick the best they support
            supported = ctranslate2.get_supported_compute_types("cuda")
            compute = next(t for t in ("float16", "int8_float16", "int8", "float32") if t in supported)
            model = WhisperModel(name, device="cuda", compute_type=compute)
            print(f"Using GPU ({compute}).")
            return model, "cuda"
        except Exception as e:
            if device == "cuda":
                raise
            print(f"GPU not available ({e.__class__.__name__}), using CPU (slower but fine).")
    return WhisperModel(name, device="cpu", compute_type="int8"), "cpu"


def gpu_failed(error):
    """True if the error looks like the GPU running out of memory (or CUDA giving up) mid-run."""
    msg = str(error).lower()
    return isinstance(error, RuntimeError) and ("out of memory" in msg or "cuda" in msg or "cublas" in msg)


def fits(text):
    return len(textwrap.wrap(text, MAX_LINE_CHARS)) <= MAX_LINES


def join(words):
    return "".join(w.word for w in words).strip()


def build_cues(words):
    """Group words into cues that fit 2 lines x 42 chars and read naturally.

    Returns [start, end, text, speaker] lists; a new cue starts whenever the speaker changes.
    """
    cues, current = [], []

    def flush(upto=None):
        upto = len(current) if upto is None else upto
        chunk = current[:upto]
        if chunk:
            cues.append([chunk[0].start, chunk[-1].end, join(chunk), chunk[0].speaker])
        del current[:upto]

    for w in words:
        if current:
            too_long = not fits(join(current + [w]))
            too_slow = w.end - current[0].start > MAX_CUE_SECONDS
            paused = w.start - current[-1].end > PAUSE_SPLIT_SECONDS
            sentence_done = current[-1].word.strip().endswith((".", "?", "!"))
            new_speaker = w.speaker != current[-1].speaker
            if paused or sentence_done or new_speaker:
                flush()
            elif too_long or too_slow:
                # Break after the last comma in the back half, so a short tail isn't orphaned
                comma = max((i for i, x in enumerate(current) if x.word.strip().endswith(",")), default=-1)
                flush(comma + 1 if comma >= len(current) // 2 else None)
        current.append(w)
    flush()

    # Give short cues time to be read, without overlapping the next one
    for i, cue in enumerate(cues):
        if cue[1] - cue[0] < MIN_CUE_SECONDS:
            limit = cues[i + 1][0] - GAP_BETWEEN_CUES if i + 1 < len(cues) else float("inf")
            cue[1] = min(cue[0] + MIN_CUE_SECONDS, limit)
    return cues


def wrap(text):
    """Split into at most 2 lines, as evenly as possible."""
    if len(text) <= MAX_LINE_CHARS:
        return text
    for width in range(len(text) // 2 + 1, MAX_LINE_CHARS + 1):
        lines = textwrap.wrap(text, width)
        if len(lines) <= MAX_LINES:
            return "\n".join(lines)
    return "\n".join(textwrap.wrap(text, MAX_LINE_CHARS))


def timestamp(seconds):
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def clock(seconds):
    """Short m:ss (or h:mm:ss) for showing times to people."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def unique_path(path):
    """path, or 'name (2).srt', 'name (3).srt'... if it already exists, so nothing is overwritten."""
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def output_path(video, out_dir):
    """Where the .srt goes: the chosen subtitles folder, or next to the video."""
    if out_dir:
        folder = Path(out_dir)
        if folder.is_dir():
            return folder / f"{video.stem}.srt"
        print(f"The subtitles folder {folder} isn't there (unplugged drive?), so they're going next to the video.")
    return video.with_suffix(".srt")


def remember(args, status, **details):
    """Add this run to the history the window shows."""
    with contextlib.suppress(OSError):
        add_history({"id": datetime.datetime.now().strftime("%Y%m%d%H%M%S%f"),
                     "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                     "input": str(Path(args.input).absolute()), "status": status,
                     "level": args.level, "model": args.model or LEVELS[args.level]["model"], **details})


def write_srt(path, cues, names=None):
    """Write cues to an .srt; with names, prefix each line with who is speaking."""
    with open(path, "w", encoding="utf-8") as f:
        for i, (start, end, text, speaker) in enumerate(cues, 1):
            if names:
                text = f"{names[speaker]}: {text}"
            f.write(f"{i}\n{timestamp(start)} --> {timestamp(end)}\n{wrap(text)}\n\n")


def set_title(text):
    """Show text in the console window's title bar."""
    if not IN_CONSOLE:
        return
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW(text)
    else:
        sys.stdout.write(f"\33]0;{text}\a")
        sys.stdout.flush()


def announce_done(path):
    """Beep and open the folder with the new file selected."""
    if not IN_CONSOLE:
        return
    try:
        if sys.platform == "win32":
            import winsound

            winsound.MessageBeep()
            subprocess.Popen(f'explorer /select,"{path}"')
        elif sys.platform == "darwin":
            print("\a", end="", flush=True)
            subprocess.Popen(["open", "-R", str(path)])
        else:
            print("\a", end="", flush=True)
            subprocess.Popen(["xdg-open", str(path.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def wav_bytes(clip):
    """A 16 kHz float32 clip as the bytes of a 16-bit mono .wav file."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(clip, -1, 1) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


class VoicePlayer:
    """Plays short clips without blocking, so people can type a name while it plays."""

    def __init__(self):
        self.process = None
        self.files = []

    def play(self, clip):
        """Start playing a 16 kHz float32 clip; returns False if this computer has no way to play it."""
        self.stop()
        fd, name = tempfile.mkstemp(suffix=".wav", prefix="speaker-")
        with os.fdopen(fd, "wb") as f:
            f.write(wav_bytes(clip))
        self.files.append(name)
        try:
            if sys.platform == "win32":
                import winsound

                winsound.PlaySound(name, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return True
            players = [["afplay"]] if sys.platform == "darwin" else [["paplay"], ["aplay", "-q"],
                                                                     ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]]
            for cmd in players:
                try:
                    self.process = subprocess.Popen(cmd + [name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except OSError:
                    continue
        except RuntimeError:
            pass
        return False

    def stop(self):
        if sys.platform == "win32":
            import winsound

            winsound.PlaySound(None, 0)
        elif self.process and self.process.poll() is None:
            self.process.terminate()
        self.process = None

    def close(self):
        self.stop()
        for name in self.files:
            with contextlib.suppress(OSError):
                os.remove(name)


def voice_clip(audio, spk, turns):
    """About VOICE_CLIP_SECONDS of this speaker, taken from their longest turns, in the order they were said."""
    longest = sorted(((s, e) for s, e, k in turns if k == spk), key=lambda t: t[1] - t[0], reverse=True)
    chosen, total = [], 0.0
    for s, e in longest:
        take = min(e - s, VOICE_CLIP_SECONDS - total)
        if take < 0.5:
            break
        chosen.append((s, s + take))
        total += take
    gap = np.zeros(int(0.3 * SAMPLE_RATE), dtype=np.float32)
    parts = []
    for s, e in sorted(chosen):
        parts += [audio[int(s * SAMPLE_RATE):int(e * SAMPLE_RATE)], gap]
    return np.concatenate(parts) if parts else gap


def ask_name(spk, audio, turns, cues, player):
    """Show who a speaker is (talk time, longest lines, a clip of their voice) and ask for their name."""
    default = f"Speaker {spk + 1}"
    talk = sum(e - s for s, e, k in turns if k == spk)
    lines = sorted((c for c in cues if c[3] == spk), key=lambda c: len(c[2]), reverse=True)[:3]
    print(f"\n{default} talks for {clock(talk)} in total. Their longest lines:")
    for start, _, text, _ in sorted(lines):
        print(f'  [{clock(start)}]  "{text}"')

    clip = voice_clip(audio, spk, turns)
    if EVENTS:
        emit("ask_speaker", speaker=spk, label=default, talk=round(talk, 1),
             lines=[{"start": round(c[0], 1), "text": c[2]} for c in sorted(lines)],
             clip="data:audio/wav;base64," + base64.b64encode(wav_bytes(clip)).decode("ascii"))
        answer = sys.stdin.readline()
        return answer.strip() or default
    can_play = player.play(clip)
    if can_play:
        print("  (Playing a few seconds of their voice now.)")
    prompt = f"Who is {default}? Type a name" + (", P to play again" if can_play else "") + \
             f", or press Enter to keep '{default}' > "
    try:
        while True:
            answer = input(prompt).strip()
            if can_play and answer.lower() == "p":
                player.play(clip)
                continue
            return answer or default
    except EOFError:
        return default
    finally:
        player.stop()


def name_speakers(detector, audio, turns, cues, voices_dir):
    """Work out a display name for each detected speaker: voice samples first, then ask."""
    import speakers

    ids = sorted({spk for _, _, spk in turns})
    names = {}
    samples = speakers.voice_samples(voices_dir)
    if samples:
        sample_prints = {name: detector.fingerprint(decode_audio(str(path), sampling_rate=speakers.SAMPLE_RATE))
                         for name, path in samples.items()}
        matches = speakers.match_names(detector.speaker_fingerprints(audio, turns), sample_prints)
        for spk, (name, score) in matches.items():
            names[spk] = name
            print(f"Speaker {spk + 1} matched to {name} (voice similarity {score:.0%})")

    player = VoicePlayer()
    try:
        for spk in ids:
            if spk in names:
                continue
            names[spk] = f"Speaker {spk + 1}"
            if EVENTS or (sys.stdin and sys.stdin.isatty()):
                names[spk] = ask_name(spk, audio, turns, cues, player)
    finally:
        player.close()
    return names


def transcribe(whisper, model_name, audio, language, beam_size, bar):
    """Transcribe audio into Tokens, moving to the CPU if the GPU runs out of memory partway through.

    whisper is {"model": ..., "device": ...}; it is the only reference to the model, so swapping it
    for a CPU one frees the GPU memory. Returns (tokens, detected language).
    """
    tokens, done = [], 0.0  # done = seconds of audio fully transcribed so far
    while True:
        offset = done  # timestamps from this attempt are relative to where it started
        try:
            segments, info = whisper["model"].transcribe(audio[int(offset * SAMPLE_RATE):], language=language,
                                              word_timestamps=True, vad_filter=True, beam_size=beam_size)
            language = info.language  # keep it the same if we have to resume on the CPU
            for seg in segments:
                tokens.extend(Token(w.word, w.start + offset, w.end + offset, None) for w in seg.words)
                done = seg.end + offset
                bar.update(min(done, bar.total) - bar.n)
                set_title(f"{bar.n / bar.total:.0%} - Making subtitles")
            return tokens, language
        except Exception as e:
            if whisper["device"] == "cpu" or not gpu_failed(e):
                raise
            bar.write(f"The GPU ran out of memory at {clock(done)}. Carrying on with the CPU (slower)...")
            whisper["model"] = None
            whisper["model"], whisper["device"] = load_model(model_name, "cpu")


def make_subtitles(args, level):
    potato = level == "potato"
    model_name = args.model or LEVELS[level]["model"]

    if args.prepare:
        # Only what's missing is downloaded; nothing is loaded into memory
        print(f"Models live in {MODELS_DIR}")
        try:
            if models.whisper_path(model_name):
                print(f"The '{model_name}' speech model is already downloaded.")
            else:
                whisper_source(model_name)
            if potato:
                print("Potato mode: skipping the speaker detection models.")
            elif models.speakers_ready():
                print("The speaker detection models are already downloaded.")
            else:
                print("Downloading the speaker detection models (32 MB, first time only)...")
                models.download_speakers(download_progress("speakers", "the speaker detection models"))
        except Exception as e:  # no internet looks different from each library; the log has the details
            raise FriendlyError("Couldn't finish downloading. Check your internet connection and try again; "
                                "whatever already finished is kept.") from e
        print("Models ready.")
        emit("done", files=[])
        return
    if args.input is None:
        raise FriendlyError("Drag a video or audio file onto the Make Subtitles icon.")
    if not args.input.exists():
        raise FriendlyError(f"File not found: {args.input}")
    out = unique_path((args.out or output_path(args.input, args.out_dir)).absolute())
    if args.speakers is not None and potato:
        print("Potato mode: speaker detection is off, so everyone's lines go in one file without names.\n"
              "To label who is talking, run Settings.bat and pick Fast or higher.")
        args.speakers = None

    set_title(f"Loading - {args.input.name}")
    # Potato mode skips the resource readout, which polls nvidia-smi every second
    device = args.device if potato else preflight(model_name, args.device)
    try:
        audio = decode_audio(str(args.input), sampling_rate=SAMPLE_RATE)
    except Exception as e:
        raise FriendlyError(f"Couldn't read the sound in '{args.input.name}'. Is it a video or audio file?\n"
                            "If it plays fine elsewhere, try exporting it again as an .mp4 or .wav.") from e
    if not len(audio):
        raise FriendlyError(f"'{args.input.name}' has no sound in it.")
    quality = "" if args.model else f" ({LEVELS[level]['name']} quality)"
    print(f"Loading '{model_name}' model{quality}...")
    whisper = dict(zip(("model", "device"), load_model(model_name, device)))

    # Speaker detection runs on the CPU in the background while Whisper transcribes
    detector, detection = None, {}
    if args.speakers is not None:
        import speakers

        count = args.speakers or len(speakers.voice_samples(args.voices)) or -1
        print(f"Speaker detection on ({count if count > 0 else 'guessing how many'} people).")
        if not models.speakers_ready():
            print("Downloading the speaker detection models (32 MB, first time only)...")
            models.download_speakers(download_progress("speakers", "the speaker detection models"))
        detector = speakers.SpeakerDetector(count)

        def detect():
            try:
                detection["turns"] = detector.detect(audio)
            except Exception as e:
                detection["error"] = e

        detect_thread = threading.Thread(target=detect, daemon=True)
        detect_thread.start()

    # Progress is measured in seconds of audio transcribed
    total = round(len(audio) / SAMPLE_RATE, 1)
    if EVENTS:
        bar = EventBar(total, "Transcribing")
        bar.extra = (lambda: round(detector.progress, 3)) if detector else None
        bar.send()
    else:
        bar = tqdm(total=total, unit="s", dynamic_ncols=True,
                   bar_format="{desc}{percentage:3.0f}%|{bar}| {n:.0f}/{total:.0f}s [{elapsed}<{remaining}]{postfix}",
                   desc="Transcribing ")
    extra = (lambda: f"Speakers {detector.progress:.0%}") if detector else None
    # Potato still shows CPU and RAM (nearly free to read); only the GPU polling is skipped
    monitor = ResourceMonitor(bar, extra, gpu=not potato)
    with monitor or contextlib.nullcontext():
        tokens, language = transcribe(whisper, model_name, audio, args.language, 1 if potato else 5, bar)
        bar.update(bar.total - bar.n)
        if detector:
            bar.set_description_str("Finishing speaker detection ")
            detect_thread.join()
    bar.close()
    print(f"Language: {language}  Length: {clock(len(audio) / SAMPLE_RATE)}")
    emit("progress", done=total, total=total, stage="Writing subtitles", speakers=None)
    if "error" in detection:
        raise detection["error"]
    turns = detection.get("turns", [])

    if monitor:
        peaks = f"Peak RAM {monitor.peak_ram / 2**30:.1f}GB"
        if monitor.peak_vram:
            peaks = f"Peak VRAM {monitor.peak_vram / 1024:.1f}GB  " + peaks
        print(f"{peaks}  (whole system, all apps)")

    if not tokens:
        raise FriendlyError("No speech found. If there's loud music, try a version with the music muted.")
    if turns:
        tokens = [t._replace(speaker=speakers.speaker_at(t.start, t.end, turns)) for t in tokens]
    cues = build_cues(tokens)

    if not turns:
        if detector:
            print("No separate speakers found, writing plain subtitles.")
        write_srt(out, cues)
        print(f"Wrote {len(cues)} subtitles to {out}")
        files = [{"path": str(out), "name": None, "count": len(cues)}]
        remember(args, "done", files=files, length=round(total, 1), language=language, speakers=[])
        emit("done", files=files)
        return out

    names = name_speakers(detector, audio, turns, cues, args.voices)
    write_srt(out, cues, names)
    print(f"\nWrote {len(cues)} labelled subtitles to {out}")
    files = [{"path": str(out), "name": None, "count": len(cues)}]
    for spk, name in names.items():
        spk_cues = [c for c in cues if c[3] == spk]
        if spk_cues:
            safe_name = "".join(ch for ch in name if ch not in '<>:"/\\|?*')
            path = unique_path(out.with_name(f"{out.stem} - {safe_name}.srt"))
            write_srt(path, spk_cues)
            print(f"  {name}: {len(spk_cues)} subtitles -> {path.name}")
            files.append({"path": str(path), "name": name, "count": len(spk_cues)})
    remember(args, "done", files=files, length=round(total, 1), language=language, speakers=list(names.values()))
    emit("done", files=files)
    return out


def main():
    settings = load_settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, nargs="?", help="video or audio file")
    parser.add_argument("--prepare", action="store_true",
                        help="just download the models (used by Setup), so later runs work offline")
    parser.add_argument("--level", choices=list(LEVELS), default=settings["level"],
                        help=f"quality level (default: {settings['level']}, from Settings)")
    parser.add_argument("--model", help="a specific Whisper model instead of the level's, "
                                        "e.g. tiny, base, small, medium, large-v3-turbo, large-v3")
    parser.add_argument("--language", default=settings["language"],
                        help=f"e.g. en, or 'auto' to detect it (default: {settings['language'] or 'auto'}, from Settings)")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--out", type=Path, help="output .srt path (default: next to input, or the subtitles folder)")
    parser.add_argument("--out-dir", type=Path, default=settings.get("output_dir"),
                        help="folder to save subtitles in (default: "
                             f"{settings.get('output_dir') or 'next to each video'}, from the window)")
    parser.add_argument("--speakers", type=int, metavar="N",
                        help="also detect who is talking: number of people, or 0 to guess "
                             "(or use how many clips are in the Voices folder)")
    parser.add_argument("--voices", type=Path, default=VOICES_DIR, help="folder of voice clips named after each person")
    parser.add_argument("--events", action="store_true", help=argparse.SUPPRESS)  # used by the app window
    args = parser.parse_args()
    if args.events:
        global EVENTS
        EVENTS = True
        # The window reads this through a pipe: send each line as soon as it is printed, in UTF-8
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stdin.reconfigure(encoding="utf-8")
    if args.language == "auto":
        args.language = None

    try:
        out = make_subtitles(args, args.level)
    except KeyboardInterrupt:
        sys.exit("\nStopped.")
    except FriendlyError as e:
        if e.__cause__:
            log_error(e.__cause__)
        if args.input and not args.prepare:
            remember(args, "failed", error=str(e))
        emit("error", message=str(e), log=None)
        sys.exit(f"\n{e}")
    except Exception as e:
        log_error(e)
        if args.input and not args.prepare:
            remember(args, "failed", error=f"{e.__class__.__name__}: {e}")
        emit("error", message=f"Something went wrong: {e.__class__.__name__}: {e}", log=str(ERROR_LOG))
        sys.exit(f"\nSomething went wrong: {e.__class__.__name__}: {e}\n"
                 f"The full details are saved in {ERROR_LOG}\n"
                 "Send that file along if you ask for help.")
    if out:
        set_title(f"Done - {out.name}")
        announce_done(out)


def log_error(error):
    with contextlib.suppress(OSError):
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        ERROR_LOG.write_text(f"{' '.join(sys.argv)}\n\n" +
                             "".join(traceback.format_exception(error)), encoding="utf-8")


if __name__ == "__main__":
    main()
