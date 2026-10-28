"""People you've named before: a voice fingerprint per name, kept on this PC, so the next video
with them names them by itself (Settings > Remembered voices lists and deletes them).

Fingerprints are only comparable when made by the same voice model, so each is stored with
the model's name and ones from another model are ignored.
"""

import datetime
import json

from paths import data_dir

KNOWN_FILE = data_dir() / "known_voices.json"
MATCH = 0.60    # how alike (whole-person fingerprints) to name someone without asking: the same
                # person in different recordings measured 0.88+, different people at most 0.31
MARGIN = 0.10   # ...and clearly more alike than the next closest remembered person
MAX_WEIGHT = 10  # later videos keep counting, so a voice that changes (a new mic) catches up


def _load():
    try:
        data = json.loads(KNOWN_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    KNOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    KNOWN_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")


def listing():
    """[{"name", "videos", "seen"}] for Settings, most recently heard first."""
    rows = [{"name": name, "videos": v.get("videos", 1), "seen": v.get("seen")} for name, v in _load().items()]
    return sorted(rows, key=lambda r: r["seen"] or "", reverse=True)


def forget(name):
    data = _load()
    data.pop(name, None)
    _save(data)
    return listing()


def forget_all():
    KNOWN_FILE.unlink(missing_ok=True)
    return []


def prints(model):
    """{name: fingerprint} made by this voice model."""
    import numpy as np

    return {name: np.array(v["print"]) for name, v in _load().items()
            if v.get("model") == model and isinstance(v.get("print"), list)}


def match(speaker_prints, model, skip=()):
    """{speaker: name} for detected speakers that clearly sound like someone remembered.
    speaker_prints: {speaker: fingerprint of up to 30 s of them}. Names in skip aren't used."""
    known = {name: p for name, p in prints(model).items() if name not in skip}
    found, used = {}, set()
    pairs = []
    for spk, p in speaker_prints.items():
        scores = sorted(((float(p @ q), name) for name, q in known.items()), reverse=True)
        if scores and scores[0][0] >= MATCH and (len(scores) == 1 or scores[0][0] - scores[1][0] >= MARGIN):
            pairs.append((scores[0][0], spk, scores[0][1]))
    for score, spk, name in sorted(pairs, reverse=True):  # best first, each name once
        if spk not in found and name not in used:
            found[spk] = (name, score)
            used.add(name)
    return found


def remember(named_prints, model):
    """Add or update people: {name: fingerprint}. An existing voice becomes the running average."""
    import numpy as np

    data, today = _load(), datetime.date.today().isoformat()
    for name, p in named_prints.items():
        old = data.get(name)
        if old and old.get("model") == model:
            n = min(old.get("videos", 1), MAX_WEIGHT)
            p = (np.array(old["print"]) * n + p) / (n + 1)
            videos = old.get("videos", 1) + 1
        else:
            videos = 1
        p = np.asarray(p, dtype=float)
        data[name] = {"print": [round(x, 6) for x in (p / np.linalg.norm(p)).tolist()], "model": model,
                      "videos": videos, "seen": today}
    _save(data)
