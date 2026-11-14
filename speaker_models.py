"""The speaker models, run with onnxruntime and numpy: voice fingerprints and who spoke when.

This does what sherpa-onnx 2.0.1 did for us, step for step, so the results are the same
(including the voice fingerprints people's names are remembered by, see known_voices.py). It
exists because sherpa-onnx's DLLs aren't signed, and Windows 11's Smart App Control (or a
school's or company's app rules) refuses to load them; onnxruntime's are signed by Microsoft,
and faster-whisper needs it anyway.

The sherpa-onnx code it follows (k2-fsa/sherpa-onnx, tag v2.0.1, sherpa-onnx/csrc/):
  speaker-embedding-extractor-nemo-impl.h      the TitaNet fingerprints (VOICE_MODEL)
  speaker-embedding-extractor-general-impl.h   the WeSpeaker fingerprints (EMB_MODEL)
  features.cc / features.h                     the filterbank settings each one uses
  offline-speaker-diarization-pyannote-impl.h  who spoke when, with segmentation-3.0 (SEG_MODEL)
  fast-clustering.cc                           grouping the fingerprints into people
and kaldi-native-fbank v1.22.4 (feature-window.cc, feature-fbank.cc, mel-computations.cc) for
the filterbank itself, and hclust-cpp (fastcluster) for the grouping. Where sherpa works in
32-bit floats and the rounding decides something (a frame index, a sample index), this does the
same sum in 32-bit floats. tests/test_speaker_models.py checks the results against sherpa-onnx's.
(sherpa-onnx and kaldi-native-fbank are Apache-2.0 licensed, hclust-cpp BSD-style.)
"""

import numpy as np
import onnxruntime as ort

FLT_EPSILON = np.float32(np.finfo(np.float32).eps)
f32 = np.float32


def session(path, threads):
    """An onnxruntime session on the processor, with this many threads for each model run."""
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, int(threads))
    options.inter_op_num_threads = 1
    options.log_severity_level = 3  # errors only: warnings on stderr would look like a failure's message
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


# ---- the filterbank (kaldi-native-fbank) --------------------------------------------------------

def _slaney_mel(hz):
    with np.errstate(divide="ignore"):  # (log(0) in the branch np.where doesn't pick)
        return np.where(hz <= 1000, hz * f32(3) / f32(200), f32(15) + f32(14.545078505785561) * np.log(hz / f32(1000)))


def _slaney_hz(mel):
    return np.where(mel <= 15, f32(200) / f32(3) * mel, f32(1000) * np.exp((mel - f32(15)) * f32(0.06875177742094911)))


def _htk_mel(hz):
    return f32(1127) * np.log(f32(1) + hz / f32(700))


def mel_banks(num_bins, sample_rate, padded, low, high, librosa):
    """[padded // 2 + 1, num_bins] triangle weights, as knf's MelBanks builds them (in float32)."""
    width = f32(sample_rate) / f32(padded)
    bins = padded // 2 + (1 if librosa else 0)  # Kaldi's banks leave out the top (Nyquist) bin
    freqs = width * np.arange(bins, dtype=np.float32)
    weights = np.zeros((padded // 2 + 1, num_bins), dtype=np.float32)
    to_mel = _slaney_mel if librosa else _htk_mel
    mel_low, mel_high = to_mel(f32(low)), to_mel(f32(high))
    delta = (mel_high - mel_low) / f32(num_bins + 1)
    edges = mel_low + np.arange(num_bins + 2, dtype=np.float32) * delta
    if librosa:
        left, centre, right = (_slaney_hz(edges[k:k + num_bins]).astype(np.float32) for k in (0, 1, 2))
        x = freqs[:, None]
    else:
        left, centre, right = edges[:num_bins], edges[1:num_bins + 1], edges[2:]
        x = _htk_mel(freqs)[:, None]
    inside = (x > left) & (x < right)
    rising = (x - left) / (centre - left)
    falling = (right - x) / (right - centre)
    w = np.where(x <= centre, rising, falling)
    if librosa:  # Slaney normalisation: each triangle has the same area
        w = w * (f32(2) / (right - left))
    weights[:bins] = np.where(inside, w, 0).astype(np.float32)
    return weights


def _window(kind, size):
    i = np.arange(size, dtype=np.float64)
    if kind == "hann":  # periodic
        return 0.5 - 0.5 * np.cos(2 * np.pi / size * i)
    a = 2 * np.pi / (size - 1)
    if kind == "povey":
        return (0.5 - 0.5 * np.cos(a * i)) ** 0.85
    if kind == "hamming":
        return 0.54 - 0.46 * np.cos(a * i)
    if kind == "hanning":
        return 0.5 - 0.5 * np.cos(a * i)
    if kind == "rectangular":
        return np.ones(size)
    raise ValueError(f"Unsupported window: {kind}")


class Fbank:
    """Log mel filterbank features, [frames, num_bins], computed the way knf's OnlineFbank does
    for one whole waveform. Dither is always off, so the same audio always gives the same numbers."""

    BLOCK = 4096  # frames at a time, so a long voice clip doesn't need a huge spectrum in memory

    def __init__(self, sample_rate=16000, num_bins=80, frame_length_ms=25.0, frame_shift_ms=10.0,
                 snip_edges=False, remove_dc_offset=True, preemph=0.97, window="povey",
                 low_freq=20.0, high_freq=-400.0, librosa=False, scale=1.0):
        self.size = int(f32(sample_rate) * f32(0.001) * f32(frame_length_ms))
        self.shift = int(f32(sample_rate) * f32(0.001) * f32(frame_shift_ms))
        self.padded = 1 << (self.size - 1).bit_length()
        self.snip_edges, self.remove_dc, self.preemph, self.scale = snip_edges, remove_dc_offset, preemph, scale
        self.window = _window(window, self.size)
        high = high_freq if high_freq > 0 else sample_rate / 2 + high_freq
        self.banks = mel_banks(num_bins, sample_rate, self.padded, low_freq, high, librosa).astype(np.float64)
        self.num_bins = num_bins

    def num_frames(self, n):
        if self.snip_edges:
            return 0 if n < self.size else 1 + (n - self.size) // self.shift
        return (n + self.shift // 2) // self.shift

    def __call__(self, samples):
        x = np.asarray(samples, dtype=np.float32).astype(np.float64)
        if self.scale != 1.0:
            x = x * self.scale
        frames = self.num_frames(len(x))
        if frames == 0:
            return np.zeros((0, self.num_bins), dtype=np.float32)
        if self.snip_edges:
            first = 0
        else:  # frames centred on each shift; the edges are mirrored (sample -1 is sample 0)
            first = self.shift // 2 - self.size // 2
            right = max(0, (frames - 1) * self.shift + first + self.size - len(x))
            x = np.pad(x, (-first, right), mode="symmetric")
            first = 0
        windows = np.lib.stride_tricks.sliding_window_view(x[first:], self.size)[::self.shift][:frames]
        out = np.empty((frames, self.num_bins), dtype=np.float32)
        for a in range(0, frames, self.BLOCK):
            w = windows[a:a + self.BLOCK].copy()
            if self.remove_dc:
                w -= w.mean(axis=1, keepdims=True)
            if self.preemph:
                w[:, 1:] -= self.preemph * w[:, :-1].copy()
                w[:, 0] -= self.preemph * w[:, 0]
            w *= self.window
            spectrum = np.fft.rfft(w, n=self.padded)
            power = spectrum.real ** 2 + spectrum.imag ** 2
            energies = power @ self.banks
            out[a:a + self.BLOCK] = np.log(np.maximum(energies, FLT_EPSILON))
        return out


# ---- voice fingerprints -------------------------------------------------------------------------

class Embedder:
    """A speaker embedding model (NeMo, WeSpeaker or 3D-Speaker), as sherpa-onnx's
    SpeakerEmbeddingExtractor ran it. compute(samples) gives the raw (unnormalised) embedding."""

    def __init__(self, path, threads=4):
        self.session = session(path, threads)
        meta = self.session.get_modelmeta().custom_metadata_map
        self.framework = meta.get("framework", "")
        self.sample_rate = int(meta.get("sample_rate", 16000))
        self.dim = int(meta["output_dim"])
        self.normalize = meta.get("feature_normalize_type", "")
        self.inputs = [i.name for i in self.session.get_inputs()]
        if self.framework == "nemo":
            # speaker-embedding-extractor-nemo-impl.h: CreateStream()
            self.fbank = Fbank(self.sample_rate, int(meta["feat_dim"]), float(meta["window_size_ms"]),
                               float(meta["window_stride_ms"]), snip_edges=True, remove_dc_offset=False,
                               window=meta.get("window_type", "povey"), low_freq=0.0, librosa=True)
            if self.normalize not in ("", "per_feature"):
                raise ValueError(f"Unsupported feature_normalize_type: {self.normalize}")
        elif self.framework in ("wespeaker", "3d-speaker"):
            # speaker-embedding-extractor-general-impl.h: CreateStream(), the rest left at the defaults
            scale = 1.0 if int(meta.get("normalize_samples", 1)) else 32768.0
            self.fbank = Fbank(self.sample_rate, 80, scale=scale)
            if self.normalize not in ("", "global-mean"):
                raise ValueError(f"Unsupported feature_normalize_type: {self.normalize}")
        else:
            raise ValueError(f"Not a speaker model sherpa-onnx knew: framework {self.framework!r}")

    def features(self, samples):
        feats = self.fbank(samples)
        if self.normalize == "per_feature":
            f = feats.astype(np.float64)
            centred = f - f.mean(axis=0)
            std = np.sqrt((centred ** 2).mean(axis=0))
            feats = (centred / (std + 1e-5)).astype(np.float32)
        elif self.normalize == "global-mean":
            feats = (feats - feats.astype(np.float64).mean(axis=0)).astype(np.float32)
        return feats

    def compute(self, samples):
        feats = self.features(samples)
        if not len(feats):
            raise ValueError("This piece of audio is too short for a voice fingerprint")
        if self.framework == "nemo":
            x = np.ascontiguousarray(feats.T[None])  # [1, feat_dim, frames]
            out = self.session.run(["embs"], {self.inputs[0]: x, self.inputs[1]: np.array([len(feats)], dtype=np.int64)})
        else:
            out = self.session.run(None, {self.inputs[0]: feats[None]})
        return np.asarray(out[0][0], dtype=np.float32)


# ---- grouping fingerprints (fast-clustering.cc: complete linkage, as hclust-cpp does it) -------

def complete_linkage(similarity):
    """The merges of complete-linkage clustering of points with this cosine similarity matrix,
    as [(height, a, b)] in the order they happen (heights rising): a and b are a point of each
    of the two groups joined. Nearest-neighbour chains, like fastcluster: about n² work in all."""
    n = len(similarity)
    d = np.maximum(1.0 - similarity.astype(np.float32), 0, dtype=np.float32)
    np.fill_diagonal(d, np.inf)
    alive = np.ones(n, dtype=bool)
    merges, chain = [], []
    for _ in range(n - 1):
        while True:
            if not chain:
                chain.append(int(np.flatnonzero(alive)[0]))
            a = chain[-1]
            row = d[a]
            b = int(np.argmin(row))
            if len(chain) > 1 and row[chain[-2]] <= row[b]:
                b = chain[-2]
            if len(chain) > 1 and b == chain[-2]:
                break
            chain.append(b)
        chain.pop()
        chain.pop()
        a, b = min(a, b), max(a, b)
        merges.append((float(d[a, b]), a, b))
        joined = np.maximum(d[a], d[b])  # complete linkage: the farthest pair
        joined[a] = np.inf
        d[a], d[:, a] = joined, joined
        d[b], d[:, b] = np.inf, np.inf
        alive[b] = False
    return sorted(merges, key=lambda m: m[0])  # stable, like generate_R_dendrogram


def cluster(embeddings, num_clusters=-1, threshold=0.5):
    """A group number for each embedding (FastClustering): num_clusters groups if that's > 0,
    otherwise groups no further apart than threshold (cosine distance, complete linkage).
    Groups are numbered in order of their first member, as cutree_k does."""
    n = len(embeddings)
    if n == 0:
        return np.zeros(0, dtype=int)
    if n == 1:
        return np.zeros(1, dtype=int)
    m = embeddings.astype(np.float32)
    m = m / np.linalg.norm(m, axis=1, keepdims=True).astype(np.float32)
    merges = complete_linkage(m @ m.T)
    if num_clusters > 0:
        groups = num_clusters
    else:
        # cutree_cdist: the merges up to the first as far apart as the threshold
        groups = n - next((k for k, (h, _, _) in enumerate(merges) if h >= threshold), n - 1)
    if groups > n or groups < 2:
        return np.zeros(n, dtype=int)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for _, a, b in merges[:n - groups]:
        parent[root(b)] = root(a)
    labels, numbers = np.empty(n, dtype=int), {}
    for i in range(n):
        labels[i] = numbers.setdefault(root(i), len(numbers))
    return labels


# ---- who spoke when (offline-speaker-diarization-pyannote-impl.h) --------------------------------

def _msvc_partial_sort_set(values, k):
    """The k indices std::partial_sort (Microsoft's, with "greater" as the order) puts first:
    the k largest, but which of several equal ones it keeps is down to how its heap works."""
    first = list(range(len(values)))
    pred = lambda i, j: values[i] > values[j]  # noqa: E731

    def push(heap, hole, top, val):
        idx = (hole - 1) >> 1
        while top < hole and pred(heap[idx], val):
            heap[hole] = heap[idx]
            hole = idx
            idx = (hole - 1) >> 1
        heap[hole] = val

    def pop_hole(heap, hole, bottom, val):
        top, idx = hole, hole
        last_non_leaf = (bottom - 1) >> 1
        while idx < last_non_leaf:
            idx = 2 * idx + 2
            if pred(heap[idx], heap[idx - 1]):
                idx -= 1
            heap[hole] = heap[idx]
            hole = idx
        if idx == last_non_leaf and bottom % 2 == 0:
            heap[hole] = heap[bottom - 1]
            hole = bottom - 1
        push(heap, hole, top, val)

    for hole in range(k // 2 - 1, -1, -1):  # make_heap over the first k
        pop_hole(first, hole, k, first[hole])
    for nxt in range(k, len(first)):
        if pred(first[nxt], first[0]):
            val = first[nxt]
            first[nxt] = first[0]
            pop_hole(first, 0, k, val)
    return first[:k]


def _top_k(count, speakers_per_frame):
    """FinalizeLabels: in each frame, the speakers_per_frame groups counted most there."""
    rows, cols = count.shape
    out = np.zeros_like(count)
    k = np.minimum(speakers_per_frame[:rows], cols)
    one = np.flatnonzero(k == 1)
    out[one, np.argmax(count[one], axis=1)] = 1  # the first of equals, as partial_sort does for one
    out[k >= cols] = 1
    for i in np.flatnonzero((k > 1) & (k < cols)):
        out[i, _msvc_partial_sort_set(count[i].tolist(), int(k[i]))] = 1
    return out


def _cpp_mod(a, b):
    return int(np.fmod(a, b))  # C++'s %: the sign of a, so it's never > 0 for a <= 0


class Diarizer:
    """sherpa-onnx's OfflineSpeakerDiarization with pyannote segmentation-3.0, a speaker
    embedding model and FastClustering. process() gives [(start, end, speaker)] sorted by start."""

    MIN_DURATION_ON = f32(0.3)   # shorter turns are dropped
    MIN_DURATION_OFF = f32(0.5)  # a speaker's turns closer than this are joined
    WINDOW_SHIFT_RATIO = 0.1     # each 10 s window starts 1 s after the last
    BATCH = 4                    # segmentation windows run together

    def __init__(self, segmentation, embedding, threads=4, num_clusters=-1, threshold=0.5):
        self.seg = session(segmentation, threads)
        meta = self.seg.get_modelmeta().custom_metadata_map
        self.sample_rate = int(meta["sample_rate"])
        self.window_size = int(meta["window_size"])
        self.window_shift = max(1, min(self.window_size, int(self.WINDOW_SHIFT_RATIO * self.window_size)))
        self.rf_shift = int(meta["receptive_field_shift"])
        self.rf_size = int(meta["receptive_field_size"])
        self.num_speakers = int(meta["num_speakers"])
        if int(meta["powerset_max_classes"]) != 2:
            raise ValueError("Only segmentation models with up to 2 speakers at once are supported")
        self.mapping = self._powerset(int(meta["num_classes"]), self.num_speakers)
        self.embedder = Embedder(embedding, threads)
        self.num_clusters, self.threshold = num_clusters, threshold
        self.seg_input = self.seg.get_inputs()[0].name

    @staticmethod
    def _powerset(num_classes, num_speakers):
        mapping, k = np.zeros((num_classes, num_speakers), dtype=np.int32), 1
        for j in range(num_speakers):
            mapping[k, j] = 1
            k += 1
        for j in range(num_speakers):
            for m in range(j + 1, num_speakers):
                mapping[k, j] = mapping[k, m] = 1
                k += 1
        return mapping

    # -- the steps --

    def segment(self, audio):
        """Each 10 s window's speakers, frame by frame: [chunks] of [frames, 3] 0/1."""
        n, size, shift = len(audio), self.window_size, self.window_shift
        if n <= size:
            starts, last = [], True
        else:
            starts = list(range(0, (n - size) // shift * shift + 1, shift))
            last = (n - size) % shift > 0
        chunks = [(s, audio[s:s + size]) for s in starts]
        if last:
            begin = starts[-1] + shift if starts else 0
            chunks.append((begin, audio[begin:n]))
        labels = []
        for a in range(0, len(chunks), self.BATCH):
            batch = np.zeros((len(chunks[a:a + self.BATCH]), 1, size), dtype=np.float32)
            for row, (_, piece) in enumerate(chunks[a:a + self.BATCH]):
                batch[row, 0, :len(piece)] = piece
            out = self.seg.run(None, {self.seg_input: batch})[0]
            labels.extend(self.mapping[np.argmax(o, axis=1)] for o in out)
        return labels

    def _chunk_start(self, i):
        return int(np.float64(f32(i) * f32(self.window_shift) / f32(self.rf_shift)) + 0.5)

    def _frames(self, chunks):
        return (self.window_size + (chunks - 1) * self.window_shift) // self.rf_shift + 1

    def speakers_per_frame(self, labels):
        frames = self._frames(len(labels))
        count, weight = np.zeros(frames, dtype=np.float32), np.zeros(frames, dtype=np.float32)
        for i, lab in enumerate(labels):
            s = self._chunk_start(i)
            count[s:s + len(lab)] += lab.sum(axis=1)
            weight[s:s + len(lab)] += 1
        return (count / (weight + f32(1e-12)) + f32(0.5)).astype(np.int32)

    def speaker_samples(self, labels):
        """[((chunk, local speaker), [(first sample, end sample)])] for each speaker heard on
        their own for 10 frames or more in a window."""
        out = []
        size = f32(self.window_size)
        for chunk, lab in enumerate(labels):
            alone = np.where(lab.sum(axis=1, keepdims=True) < 2, lab, 0)  # overlapped speech left out
            frames = len(alone)
            offset = f32(chunk * self.window_shift)
            for spk in range(alone.shape[1]):
                d = alone[:, spk]
                if d.sum() < 10:
                    continue
                edges = np.diff(np.concatenate(([0], d, [0])))
                begins, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
                ends = np.where(ends == frames, frames - 1, ends)  # still talking at the end
                to_sample = lambda k: (f32(k) / f32(frames) * size + offset).astype(np.int32)  # noqa: E731
                out.append(((chunk, spk), list(zip(to_sample(begins).tolist(), to_sample(ends).tolist()))))
        return out

    def result(self, final):
        """[(start, end, speaker)] from the final [frames, speakers] 0/1 labels."""
        scale = f32(self.rf_shift) / f32(self.sample_rate)
        offset = f32(0.5 * self.rf_size / self.sample_rate)
        frames = len(final)
        found = []
        for spk in range(final.shape[1]):
            d = (final[:, spk] > 0).astype(np.int8)
            edges = np.diff(np.concatenate(([0], d, [0])))
            begins, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
            ends = np.where(ends == frames, frames - 1, ends)
            segs = []
            for b, e in zip(begins, ends):
                start, end = f32(b) * scale + offset, f32(e) * scale + offset
                if segs and segs[-1][1] < start and segs[-1][1] + self.MIN_DURATION_OFF >= start:
                    segs[-1][1] = end
                else:
                    segs.append([start, end])
            found += [(float(s), float(e), spk) for s, e in segs if e - s > self.MIN_DURATION_ON]
        return sorted(found, key=lambda t: (t[0], t[2]))

    def process(self, audio, callback=None):
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        n = len(audio)
        if n <= 0:
            return []
        labels = self.segment(audio)
        if len(labels) == 1:
            if callback:
                callback(1, 1)
            final = labels[0]
            if _cpp_mod(n - self.window_size, self.window_shift) > 0:
                final = final[:min(n // self.rf_shift, len(final))]
            return self.result(final)
        per_frame = self.speakers_per_frame(labels)
        if per_frame.max() == 0:
            return []
        pairs = self.speaker_samples(labels)
        embeddings, kept = [], []
        for k, (pair, spans) in enumerate(pairs):
            pieces = [audio[a:min(b, n)] for a, b in spans if min(b, n) - a > 0]
            clip = np.concatenate(pieces) if pieces else np.zeros(0, np.float32)
            # (Only in the padding after the end, there's nothing to fingerprint: sherpa-onnx
            # stopped with an error there; this leaves that speaker out, as it does a NaN.)
            vec = self.embedder.compute(clip) if self.embedder.fbank.num_frames(len(clip)) else None
            if vec is not None and not np.isnan(vec).any():
                embeddings.append(vec)
                kept.append(pair)
            if callback:
                callback(k + 1, len(pairs))
        if not embeddings:
            return []
        groups = cluster(np.stack(embeddings), self.num_clusters, self.threshold)
        to_group = dict(zip(kept, groups.tolist()))
        num_groups = int(groups.max()) + 1
        frames = self._frames(len(labels))
        count = np.zeros((frames, num_groups), dtype=np.int32)
        for chunk, lab in enumerate(labels):
            relabelled = np.zeros((len(lab), num_groups), dtype=np.int32)
            for spk in range(lab.shape[1]):
                if (chunk, spk) in to_group:
                    relabelled[:, to_group[(chunk, spk)]] |= lab[:, spk]
            s = self._chunk_start(chunk)
            count[s:s + len(lab)] += relabelled
        if (n - self.window_size) % self.window_shift > 0:
            count = count[:min(n // self.rf_shift, len(count) - 1) + 1]
        return self.result(_top_k(count, per_frame))
