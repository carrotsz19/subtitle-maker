"""How much of this PC Subtitle Maker may use (Settings > Hardware).

Every limit here is enforced on the worker process (make_subtitles.py --serve) and everything it
starts (the speaker step, nvidia-smi), so it covers every video, the queue and speaker detection:

- Processor: a share of the whole processor. Windows holds the worker to it (a Job Object's hard
  CPU cap, see winextras.limit_job), and the speech and speaker models get that many threads, so the
  work isn't spread thin over threads that keep being paused.
- Let other apps go first: the worker runs at below-normal priority (its children inherit it).
- Memory: a Job Object memory limit across the worker and its children. Going over makes the next
  allocation fail, so that's caught and said plainly (memory_message), and a pre-flight check warns
  first (memory_check).
- Graphics card: on or off (the existing "device" setting), and a smaller number format on cards
  with float16 (int8_float16: the model's weights take about half the graphics memory). A graphics
  card can't be held to a share of its time, and CTranslate2 has no graphics memory cap, so neither
  is offered.

The worker reads its limits from the environment the app starts it with (worker_env), so a change
applies from the next video: the app starts a fresh worker when the limits differ.

This module stays light (no psutil, no ctypes) so the tests and both processes can use it.
"""

import math
import os

from paths import AUDIO_RAM_MB_PER_MINUTE, LEVELS, SPEAKERS_RAM_MB

# The settings the presets set. "device" (graphics card or processor) is its own choice.
KEYS = ("cpu_percent", "low_priority", "memory_limit_gb", "gpu_low_memory")
PRESETS = {
    # 0.3.0 exactly: normal priority, the libraries' own thread counts, no limits
    "full": {"cpu_percent": 100, "low_priority": False, "memory_limit_gb": 0, "gpu_low_memory": False},
    # The default ("Share nicely"): no caps, other apps go first (about as fast; a little slower
    # while they're busy)
    "share": {"cpu_percent": 100, "low_priority": True, "memory_limit_gb": 0, "gpu_low_memory": False},
    # A quarter of the processor at most, and the smaller format on the graphics card
    "light": {"cpu_percent": 25, "low_priority": True, "memory_limit_gb": 0, "gpu_low_memory": True},
}
# faster-whisper's own default (cpu_threads=0 means 4) and speakers.py's: what "no limit" uses.
# On the GTX 1050 PC (12 threads) that's about a third of the processor for each.
DEFAULT_THREADS = 4
THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS")
# What the worker reads (make_subtitles.py, speakers.py)
ENV_THREADS, ENV_CPU, ENV_MEMORY, ENV_GPU_LOW = ("SUBTITLE_MAKER_THREADS", "SUBTITLE_MAKER_CPU_PERCENT",
                                                 "SUBTITLE_MAKER_MEMORY_MB", "SUBTITLE_MAKER_GPU_LOW_MEMORY")
ENV_IN_JOB = "SUBTITLE_MAKER_IN_JOB"  # the worker is in a Job Object: nvidia-smi is started outside it
# A video a little over the memory limit by the estimate is let through without a warning: the
# estimates are rounded up, and 4 GB shouldn't warn for one estimated at 4.1
MEMORY_TOLERANCE = 1.05
# A video of this length is assumed when the pre-flight check doesn't know how long it is
ASSUMED_MINUTES = 60

MEMORY_MESSAGE = ("It needed more memory than you allowed. Try a lower quality, or raise the limit in "
                  "Settings > Hardware.")
OUT_OF_MEMORY = "This PC ran out of memory. Close some other apps, or try a lower quality."


def preset_of(settings, gpu_low_memory=True):
    """"full", "share" or "light" if the settings are exactly one of those, else "custom".
    gpu_low_memory=False (a card where that setting changes nothing, or none): it's left out."""
    keys = [k for k in KEYS if gpu_low_memory or k != "gpu_low_memory"]
    return next((name for name, values in PRESETS.items() if all(settings.get(k) == values[k] for k in keys)),
                "custom")


def threads_for(cpu_percent, cores):
    """Threads each model (and numpy) gets under a processor limit, or None to leave them as they are.

    A share of the processor is that share of its threads (logical processors), at least one. Only
    a share smaller than the 4 threads the models use anyway sets a count: setting the thread
    variables at all, even to 4, made a 3-minute video take 35% longer on a 12-thread PC (numpy's
    OpenBLAS waits differently), while the Job Object's cap alone cost nothing measurable."""
    if cpu_percent >= 100:
        return None
    share = max(1, math.ceil(cores * cpu_percent / 100))
    return share if share < DEFAULT_THREADS else None


def limits(settings, cores=None):
    """What the worker is started with, from the saved settings. Two workers with the same limits
    are the same; different ones mean starting a fresh worker for the next video."""
    cores = cores or os.cpu_count() or 1
    cpu = settings["cpu_percent"]
    return {
        "threads": threads_for(cpu, cores),
        "cpu_percent": cpu if cpu < 100 else None,
        "memory_mb": settings["memory_limit_gb"] * 1024 or None,
        "low_priority": bool(settings["low_priority"]),
        "gpu_low_memory": bool(settings["gpu_low_memory"]),
        # Graphics card or processor: switching restarts the worker, which lets go of the graphics memory
        "device": settings["device"],
    }


def worker_env(settings, base=None, cores=None):
    """The worker's environment: base (os.environ by default) plus the limits. Unless a processor
    limit means fewer threads, the thread variables are left as they were, as before."""
    env = dict(os.environ if base is None else base)
    lim = limits(settings, cores)
    for key in (ENV_THREADS, ENV_CPU, ENV_MEMORY, ENV_GPU_LOW):
        env.pop(key, None)
    if lim["threads"]:
        for var in THREAD_VARS:
            env[var] = str(lim["threads"])
        env[ENV_THREADS] = str(lim["threads"])
    if lim["cpu_percent"]:
        env[ENV_CPU] = str(lim["cpu_percent"])
    if lim["memory_mb"]:
        env[ENV_MEMORY] = str(lim["memory_mb"])
    if lim["gpu_low_memory"]:
        env[ENV_GPU_LOW] = "1"
    return env


def env_int(key, env=None):
    """In the worker: a limit it was started with (ENV_THREADS, ENV_CPU, ENV_MEMORY), or None."""
    value = (os.environ if env is None else env).get(key, "")
    return int(value) if value.isdigit() and int(value) > 0 else None


def compute_type(supported, low_memory=False):
    """The graphics card's number format: the fastest it supports, or with low_memory the one that
    takes least memory (int8 weights) while still fast. Older cards (GTX 10-series) have no fast
    float16, so they get int8 either way."""
    order = (("int8_float16", "int8", "float16", "int8_float32", "float32") if low_memory
             else ("float16", "int8_float16", "int8", "int8_float32", "float32"))
    return next(t for t in order if t in supported)


def saves_gpu_memory(supported):
    """Does "Use less graphics memory" change anything on a card with these formats?"""
    return compute_type(supported) != compute_type(supported, low_memory=True)


def device_for(settings, hw, level):
    """Where a level will run: "cuda" or "cpu" (as make_subtitles.preflight decides for Automatic)."""
    if settings["device"] == "cpu" or not (hw or {}).get("gpu"):
        return "cpu"
    if settings["device"] == "cuda":
        return "cuda"
    vram = (hw or {}).get("vram_mb") or 0
    return "cuda" if vram - 500 >= LEVELS[level]["vram_mb"] else "cpu"


def memory_needed_mb(level, device, speakers=False, minutes=ASSUMED_MINUTES):
    """About how much memory a video takes at this quality: the model, speaker detection if it's
    on (never with Potato), and the sound of a video this long."""
    need = LEVELS[level]["ram_mb"]["cuda" if device == "cuda" else "cpu"]
    if speakers and level != "potato":
        need += SPEAKERS_RAM_MB
    return int(need + AUDIO_RAM_MB_PER_MINUTE * max(0, minutes))


def memory_check(settings, hw, speakers=False, minutes=ASSUMED_MINUTES):
    """The pre-flight check: will a video fit in the memory limit? fits is True with no limit."""
    level = settings["level"]
    need = memory_needed_mb(level, device_for(settings, hw, level), speakers, minutes)
    limit = settings["memory_limit_gb"] * 1024 or None
    return {"need_mb": need, "limit_mb": limit, "fits": not limit or need <= limit * MEMORY_TOLERANCE}


# Windows' "out of memory" process exit codes: STATUS_NO_MEMORY, STATUS_COMMITMENT_LIMIT
MEMORY_EXIT_CODES = {0xC0000017, 0xC000012D}
MEMORY_WORDS = ("memoryerror", "bad_alloc", "bad allocation", "unable to allocate", "cannot allocate memory",
                "not enough memory", "paging file is too small", "out of memory", "failed to allocate",
                "insufficient memory")


def is_memory_error(error=None, code=None):
    """Does this error (an exception or its text) or exit code mean memory ran out? The graphics
    card running out ("CUDA ... out of memory") isn't this: that falls back to the processor."""
    if code is not None and (code & 0xFFFFFFFF) in MEMORY_EXIT_CODES:
        return True
    if error is None:
        return False
    if isinstance(error, MemoryError):
        return True
    text = f"{error.__class__.__name__}: {error}" if isinstance(error, BaseException) else str(error)
    text = text.lower()
    if "cuda" in text or "cublas" in text or "cudnn" in text:
        return False
    return any(word in text for word in MEMORY_WORDS)


def memory_message(message, limit_mb=None, hit=False, code=None):
    """The message to show for a video that stopped: a plain one if it ran out of memory (over the
    limit in Settings, or the whole PC's), else the message as it was. hit: Windows said this
    video went over the limit. (A message meant for the user, a FriendlyError, is never passed in.)"""
    if limit_mb and (hit or is_memory_error(message, code)):
        return MEMORY_MESSAGE
    if is_memory_error(message, code):
        return OUT_OF_MEMORY
    return message


def speed_suffix(env=None):
    """Remembered speeds are kept apart for a processor limit, so a capped run doesn't make the
    next uncapped one's time left look slow."""
    cpu = env_int(ENV_CPU, env)
    return f"|cpu{cpu}" if cpu else ""
