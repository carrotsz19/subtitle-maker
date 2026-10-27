"""Opens the real window and fails unless it gets ready in time (the startup freeze, and worse)."""
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from paths import data_dir, load_settings, save_settings  # noqa: E402

READY_WITHIN = 20  # seconds; it took about 60 when it froze

settings = load_settings()
save_settings(settings)  # set up, so it opens on the main screen, not the setup wizard
log = data_dir() / "startup.log"
log.unlink(missing_ok=True)
start = time.time()
code = subprocess.run([sys.executable, str(ROOT / "app.py"), "--smoke-test"], timeout=180).returncode
took = time.time() - start
text = log.read_text(encoding="utf-8") if log.exists() else "(no startup.log written)"
print(text)
print(f"app.py --smoke-test ended with code {code} after {took:.1f} s")
ready = re.search(r"^\s*([\d.]+)\s+window ready to use", text, re.M)
assert code == 0, "the window didn't get ready (see startup.log above)"
assert ready, "no 'window ready to use' in startup.log"
assert float(ready.group(1)) < READY_WITHIN, f"ready after {ready.group(1)} s; more than {READY_WITHIN} s is too slow"
print(f"Window test passed: ready after {ready.group(1)} s")
