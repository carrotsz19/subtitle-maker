"""Speaker detection for make_subtitles.py: who spoke when, and which voice is whose name."""

from pathlib import Path

import numpy as np
import sherpa_onnx

from models import EMB_MODEL, SEG_MODEL, download_speakers, speakers_ready

SAMPLE_RATE = 16000

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".mov"}
MATCH_THRESHOLD = 0.35      # voice similarity needed to trust a name match
MAX_EMBED_SECONDS = 30      # how much of each speaker's audio to fingerprint
THREADS = 4


def ensure_models(progress=None):
    if not speakers_ready():
        print("Downloading the speaker detection models (32 MB, first time only)...")
        download_speakers(progress)


def voice_samples(folder):
    """{name: path} for each voice clip in the folder, named after the person (Alex.wav)."""
    if not folder or not Path(folder).is_dir():
        return {}
    return {p.stem: p for p in sorted(Path(folder).iterdir()) if p.suffix.lower() in AUDIO_EXTS}


class SpeakerDetector:
    def __init__(self, num_speakers=-1):
        ensure_models()
        embedding = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL), num_threads=THREADS)
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEG_MODEL)),
                num_threads=THREADS,
            ),
            embedding=embedding,
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers, threshold=0.5),
        )
        self.diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(embedding)
        self.progress = 0.0

    def detect(self, audio):
        """[(start, end, speaker_id)] for 16 kHz mono float32 audio."""
        def on_progress(done, total):
            self.progress = done / total if total else 1.0
            return 0

        result = self.diarizer.process(audio, callback=on_progress).sort_by_start_time()
        self.progress = 1.0
        return [(seg.start, seg.end, seg.speaker) for seg in result]

    def fingerprint(self, audio):
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=audio)
        stream.input_finished()
        vec = np.array(self.extractor.compute(stream))
        return vec / np.linalg.norm(vec)

    def speaker_fingerprints(self, audio, turns):
        clips = {}
        for start, end, spk in turns:
            clips.setdefault(spk, []).append(audio[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)])
        return {spk: self.fingerprint(np.concatenate(parts)[:MAX_EMBED_SECONDS * SAMPLE_RATE])
                for spk, parts in clips.items()}


def match_names(speaker_prints, sample_prints):
    """Pair each detected speaker with the most similar voice sample, best matches first.

    Returns {speaker_id: (name, similarity)}; speakers with no confident match are left out.
    """
    pairs = sorted(((float(sp @ sample), spk, name)
                    for spk, sp in speaker_prints.items()
                    for name, sample in sample_prints.items()), reverse=True)
    names, used = {}, set()
    for score, spk, name in pairs:
        if score < MATCH_THRESHOLD:
            break
        if spk not in names and name not in used:
            names[spk] = (name, score)
            used.add(name)
    return names


def speaker_at(start, end, turns):
    """The speaker who overlaps [start, end] the most, or the nearest one if none overlap."""
    best, best_overlap, nearest, nearest_gap = None, 0.0, None, float("inf")
    for s, e, spk in turns:
        overlap = min(end, e) - max(start, s)
        if overlap > best_overlap:
            best, best_overlap = spk, overlap
        gap = max(s - end, start - e, 0.0)
        if gap < nearest_gap:
            nearest, nearest_gap = spk, gap
    return best if best is not None else nearest
