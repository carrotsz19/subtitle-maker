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


# ---- who said each sentence ---------------------------------------------------------------------
# The detector's turns are good at *when* the voice changes but can lump two people together for
# long stretches. Whisper's sentences are a better unit: people rarely hand over mid-sentence,
# and a whole sentence (a few seconds of one voice) gives a much steadier voice fingerprint than
# the detector's half-second scraps. So: split the words into sentences, fingerprint each one,
# and group the fingerprints into people.

SENTENCE_END = (".", "?", "!", "…")
PAUSE = 0.7            # seconds of silence between words that also ends a sentence
STRONG_TURN = 1.0      # a detector turn this long on both sides of a change also ends one
MIN_PRINT = 0.8        # shorter sentences don't help decide who the people are...
SHORTEST = 0.3         # ...but down to this, a clear fingerprint still says which of them it is
LONGEST = 5.0          # longer "sentences" (Whisper running on into the next person) are split up
MAX_PRINT = 12.0       # seconds of a long sentence used for its fingerprint
SAME_GROUP = 0.84      # groups' average voices this alike are one person cut in two (with this
                       # voice model, different people measured 0.48-0.87, halves of one person 0.85+)
GROUP_MARGIN = 0.08    # how much closer to its group's voice than the next, to trust a fingerprint


def word_speakers(tokens, turns):
    return [speaker_at(t.start, t.end, turns) for t in tokens]


def sentence_spans(tokens, labels):
    """[(first, last + 1)] token ranges: a sentence ends at . ? !, a pause, or a clear change of voice."""
    def run_length(i, step):
        j, start = i, tokens[i]
        while 0 <= j + step < len(tokens) and labels[j + step] == labels[i]:
            j += step
        return abs(tokens[j].end - start.start) if step > 0 else abs(start.end - tokens[j].start)

    spans, first = [], 0
    for i in range(len(tokens) - 1):
        ends = tokens[i].word.strip().endswith(SENTENCE_END) or tokens[i + 1].start - tokens[i].end > PAUSE
        if not ends and labels[i] != labels[i + 1]:
            ends = run_length(i, -1) >= STRONG_TURN and run_length(i + 1, 1) >= STRONG_TURN
        if ends:
            spans.append((first, i + 1))
            first = i + 1
    if tokens:
        spans.append((first, len(tokens)))
    return [piece for span in spans for piece in shorter(tokens, span)]


def shorter(tokens, span):
    """Split a sentence longer than LONGEST at its longest pause, until every piece fits."""
    a, b = span
    if tokens[b - 1].end - tokens[a].start <= LONGEST or b - a < 2:
        return [span]
    middle = (tokens[a].start + tokens[b - 1].end) / 2
    # The longest pause, preferring ones near the middle so the pieces come out even
    cut = max(range(a + 1, b), key=lambda i: (tokens[i].start - tokens[i - 1].end) - abs(tokens[i].start - middle) / 20)
    return shorter(tokens, (a, cut)) + shorter(tokens, (cut, b))


def majority(tokens, labels, span):
    """The detector's speaker for most of this sentence's speaking time."""
    time = {}
    for t, label in zip(tokens[span[0]:span[1]], labels[span[0]:span[1]]):
        time[label] = time.get(label, 0) + (t.end - t.start)
    return max(time, key=time.get)


def ward(prints, count, weights=None):
    """Ward grouping into count groups: each step joins the two groups whose joining spreads
    the voices least. Unlike average linkage it doesn't leave one odd sentence as a "person"."""
    n = len(prints)
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=float)
    centres, sizes, members = prints.astype(float).copy(), w.copy(), [[i] for i in range(n)]
    alive = list(range(n))
    while len(alive) > max(1, count):
        c, z = centres[alive], sizes[alive]
        dist = ((c[:, None, :] - c[None, :, :]) ** 2).sum(-1) * (z[:, None] * z[None, :]) / (z[:, None] + z[None, :])
        np.fill_diagonal(dist, np.inf)
        i, j = np.unravel_index(np.argmin(dist), dist.shape)
        a, b = alive[i], alive[j]
        centres[a] = (centres[a] * sizes[a] + centres[b] * sizes[b]) / (sizes[a] + sizes[b])
        sizes[a] += sizes[b]
        members[a] += members[b]
        alive.remove(b)
    labels = np.empty(n, dtype=int)
    for k, a in enumerate(alive):
        labels[members[a]] = k
    return labels


def overlap(prints, labels):
    """How alike the two most alike groups sound (their average voices): high means one person
    has probably been cut in two."""
    ids = np.unique(labels)
    if len(ids) < 2:
        return -1.0
    centres = np.stack([prints[labels == g].mean(axis=0) for g in ids])
    centres /= np.linalg.norm(centres, axis=1, keepdims=True)
    sims = centres @ centres.T
    np.fill_diagonal(sims, -1)
    return float(sims.max())


def how_many(prints, lengths, most=6):
    """Guess the number of people: keep splitting while the new groups are clearly different voices."""
    k = 1
    while k < min(most, len(prints)):
        if overlap(prints, refine(prints, ward(prints, k + 1, lengths))) >= SAME_GROUP:
            break
        k += 1
    return k


def split_into_people(prints, count, lengths, detector_votes):
    """Group sentence fingerprints into people. Two candidates: Ward grouping of the voices, and
    the detector's own split tidied up by voice; whichever fits the voices better wins. (The
    detector is sometimes right where the voices are muddy, and the other way round.)"""
    k = count if count > 0 else how_many(prints, lengths)
    candidates = [refine(prints, ward(prints, k, lengths))]
    ids = {v: i for i, v in enumerate(dict.fromkeys(detector_votes))}
    if len(ids) == k:
        candidates.append(refine(prints, np.array([ids[v] for v in detector_votes])))
    # The split whose people sound most clearly different (a voice cut in two sounds alike)
    full = [c for c in candidates if len(np.unique(c)) == k] or candidates
    return min(full, key=lambda labels: overlap(prints, labels))


def closeness(prints, labels):
    """Each sentence's similarity to each group's average voice, leaving the sentence itself out
    of its own group's average (in a small group, counting itself makes any sentence look like
    it belongs). Returns (group ids, similarities [sentence, group])."""
    ids = np.unique(labels)
    sims = np.empty((len(prints), len(ids)))
    for j, g in enumerate(ids):
        members = labels == g
        total, n = prints[members].sum(axis=0), members.sum()
        for i in range(len(prints)):
            centre = total - prints[i] if members[i] else total
            count = n - 1 if members[i] else n
            sims[i, j] = prints[i] @ centre / np.linalg.norm(centre) if count else -1.0
    return ids, sims


def refine(prints, labels, rounds=3):
    """A few rounds of moving each sentence to its closest group's average voice."""
    for _ in range(rounds):
        ids, sims = closeness(prints, labels)
        new = ids[np.argmax(sims, axis=1)]
        if (new == labels).all() or len(np.unique(new)) < len(ids):  # never empty a group
            break
        labels = new
    return labels


def assign(tokens, turns, detector=None, audio=None, count=-1, samples=None):
    """Each word's speaker, decided per sentence. Returns (tokens, turns, names or None).

    Without a detector it only takes a vote per sentence. With one, it fingerprints the
    sentences and groups them; with samples ({name: fingerprint}) for everyone, it matches each
    sentence to the closest voice clip instead and names come with it. The turns returned
    follow the new labels (one per sentence), for playing back each speaker's voice.
    """
    if not tokens or not turns:
        return tokens, turns, None
    labels = word_speakers(tokens, turns)
    spans = sentence_spans(tokens, labels)
    votes = [majority(tokens, labels, s) for s in spans]
    final, names = list(votes), None

    if detector is not None and audio is not None:
        long_ones = [k for k, (a, b) in enumerate(spans) if tokens[b - 1].end - tokens[a].start >= MIN_PRINT]
        prints = []
        for k in long_ones:
            a, b = spans[k]
            start, end = tokens[a].start, min(tokens[b - 1].end, tokens[a].start + MAX_PRINT)
            prints.append(detector.fingerprint(audio[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]))
        prints = np.array(prints)
        clip_names = sorted(samples or {})
        use_clips = len(prints) and clip_names and (count <= 0 or count <= len(clip_names))
        if len(prints) >= 2 or use_clips:
            lengths = np.array([tokens[spans[k][1] - 1].end - tokens[spans[k][0]].start for k in long_ones])
            grouped = split_into_people(prints, count, lengths, [votes[k] for k in long_ones]) if len(prints) >= 2 else np.zeros(len(prints), dtype=int)
            if use_clips:
                # Name each group by the clip closest to its average voice (steadier than any one
                # sentence), best matches first, each clip used once; groups no clip fits keep a number
                ids = np.unique(grouped)
                centres = np.stack([prints[grouped == g].mean(axis=0) for g in ids])
                centres /= np.linalg.norm(centres, axis=1, keepdims=True)
                pairs = match_names({int(g): c for g, c in zip(ids, centres)}, {n: samples[n] for n in clip_names})
                relabel = {g: clip_names.index(pairs[g][0]) if g in pairs else len(clip_names) + int(g) for g in map(int, ids)}
                grouped = np.array([relabel[int(g)] for g in grouped])
            grouped = np.asarray(grouped)
            # Which group each of the detector's speakers mostly became, judged only by sentences
            # whose fingerprint is clearly in its group: short sentences fall back on it.
            # (Long sentences always go by their fingerprint: when the detector lumps two people
            # together, letting it decide unsure ones put nearly everything back in one group.)
            ids, sims = closeness(prints, grouped)
            own = sims[np.arange(len(grouped)), np.searchsorted(ids, grouped)]
            others = np.where(ids[None, :] == grouped[:, None], -np.inf, sims).max(axis=1) if len(ids) > 1 else -1
            sure = own - others >= GROUP_MARGIN
            translate = {}
            for k, g, ok in zip(long_ones, grouped, sure):
                if ok:
                    translate.setdefault(votes[k], {}).setdefault(int(g), 0)
                    translate[votes[k]][int(g)] += 1

            def detector_group(k):
                options = translate.get(votes[k])
                if not options:
                    return None
                best = max(options, key=options.get)
                return best if options[best] >= 0.7 * sum(options.values()) else None

            for k, g in zip(long_ones, grouped):
                final[k] = ("g", int(g))
            long_set = set(long_ones)
            # Short sentences ("Yeah.") are often the other person: their fingerprint decides
            # when it's clearly one person's (twice the usual margin), the detector otherwise
            group_prints = np.stack([prints[grouped == g].mean(axis=0) for g in ids])
            group_prints /= np.linalg.norm(group_prints, axis=1, keepdims=True)
            for k in range(len(spans)):
                if k in long_set:
                    continue
                a, b = spans[k]
                g = None
                if tokens[b - 1].end - tokens[a].start >= SHORTEST and len(ids) > 1:
                    sims = group_prints @ detector.fingerprint(audio[int(tokens[a].start * SAMPLE_RATE):int(tokens[b - 1].end * SAMPLE_RATE)])
                    top = np.sort(sims)
                    if top[-1] - top[-2] >= 2 * GROUP_MARGIN:
                        g = int(ids[np.argmax(sims)])
                if g is None:
                    g = detector_group(k)
                final[k] = ("g", g) if g is not None else None
            # Anything still unplaced goes with the nearest sentence that was placed, so there
            # are never more people than were asked for (or found)
            placed = [k for k in range(len(spans)) if final[k] is not None]
            for k in range(len(spans)):
                if final[k] is None:
                    middle = (tokens[spans[k][0]].start + tokens[spans[k][1] - 1].end) / 2
                    nearest = min(placed, key=lambda j: abs((tokens[spans[j][0]].start + tokens[spans[j][1] - 1].end) / 2 - middle))
                    final[k] = final[nearest]
            if use_clips:
                names = {}
    # Number the speakers in order of first appearance (Speaker 1 speaks first)
    numbering = {}
    for f in final:
        numbering.setdefault(f, len(numbering))
    if names is not None:
        names = {numbering[f]: clip_names[f[1]] for f in numbering if f[0] == "g" and f[1] < len(clip_names)}
    out, new_turns = list(tokens), []
    for (a, b), f in zip(spans, final):
        for i in range(a, b):
            out[i] = tokens[i]._replace(speaker=numbering[f])
        new_turns.append((tokens[a].start, tokens[b - 1].end, numbering[f]))
    return out, new_turns, names
