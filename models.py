"""Which models are on this PC, and downloading the ones that aren't (with progress).

Whisper models are loaded straight from their folder once they're here, so a run never asks
the internet again. This module stays light (no faster-whisper or sherpa-onnx imports) so the
app window can ask "is this level downloaded?" instantly.
"""

import fnmatch
import tarfile
import threading
import urllib.request
from pathlib import Path

from paths import LEVELS, MODELS_DIR

WHISPER_DIR = MODELS_DIR / "whisper"
# faster-whisper 1.2.1's own names for these (faster_whisper.utils._MODELS); a test checks they match
WHISPER_REPOS = {
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
# The files faster-whisper needs (the same list its own downloader uses)
WHISPER_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
NEEDED = ["config.json", "model.bin", "tokenizer.json", "vocabulary.*"]

SEG_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
SEG_MODEL = MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMB_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx")
EMB_MODEL = MODELS_DIR / "wespeaker_en_voxceleb_resnet34_LM.onnx"
SPEAKER_MB = 32  # both speaker models together, roughly


def repo_for(model):
    return model if "/" in model else WHISPER_REPOS.get(model, f"Systran/faster-whisper-{model}")


def cache_folder(model):
    return WHISPER_DIR / ("models--" + repo_for(model).replace("/", "--"))


def whisper_path(model):
    """The folder of a fully downloaded Whisper model, or None if it isn't all here."""
    snapshots = cache_folder(model) / "snapshots"
    if not snapshots.is_dir():
        return None
    for snap in sorted(snapshots.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        names = [p.name for p in snap.iterdir()] if snap.is_dir() else []
        if all(any(fnmatch.fnmatch(n, pattern) for n in names) for pattern in NEEDED) and \
                (snap / "model.bin").stat().st_size > 0:
            return snap
    return None


def speakers_ready():
    return SEG_MODEL.exists() and EMB_MODEL.exists()


def ready(level):
    """Is everything this quality level needs already on this PC?"""
    return bool(whisper_path(LEVELS[level]["model"])) and (level == "potato" or speakers_ready())


def downloaded_levels():
    return {key: ready(key) for key in LEVELS}


def folder_bytes(folder):
    return sum(p.stat().st_size for p in Path(folder).rglob("*") if p.is_file()) if Path(folder).is_dir() else 0


def download_whisper(model, progress=None):
    """Download a Whisper model (only the missing files) and return its folder.

    progress(done_mb, total_mb) is called about twice a second; total_mb is None if Hugging Face
    didn't say how big it is.
    """
    import huggingface_hub

    repo = repo_for(model)
    total = None
    try:
        info = huggingface_hub.HfApi().model_info(repo, files_metadata=True)
        total = sum(s.size or 0 for s in info.siblings
                    if any(fnmatch.fnmatch(s.rfilename, pattern) for pattern in WHISPER_FILES)) / 2**20 or None
    except Exception:
        pass  # only the size is missing; the download itself will say if the internet is down

    result, failure = {}, {}

    def work():
        try:
            result["path"] = huggingface_hub.snapshot_download(repo, cache_dir=str(WHISPER_DIR),
                                                               allow_patterns=WHISPER_FILES)
        except Exception as e:
            failure["error"] = e

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    # The download writes into the cache's blobs folder (as .incomplete files while going),
    # so its size there is how far along it is
    blobs = cache_folder(model) / "blobs"
    while thread.is_alive():
        if progress:
            progress(folder_bytes(blobs) / 2**20, total)
        thread.join(0.5)
    if failure:
        raise failure["error"]
    if progress:
        progress(total or folder_bytes(blobs) / 2**20, total)
    return Path(result["path"])


def download_speakers(progress=None):
    """Download the two speaker detection models if they're missing. progress(done_mb, total_mb)."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    done = [0.0]

    def fetch(url, target):
        def hook(blocks, block_size, size):
            if progress:
                progress(done[0] + blocks * block_size / 2**20, SPEAKER_MB)
        tmp = target.with_name(target.name + ".part")
        urllib.request.urlretrieve(url, tmp, reporthook=hook)
        tmp.replace(target)
        done[0] += target.stat().st_size / 2**20

    if not SEG_MODEL.exists():
        archive = MODELS_DIR / "segmentation.tar.bz2"
        fetch(SEG_URL, archive)
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(MODELS_DIR, filter="data")
        archive.unlink()
    if not EMB_MODEL.exists():
        fetch(EMB_URL, EMB_MODEL)
    if progress:
        progress(SPEAKER_MB, SPEAKER_MB)
