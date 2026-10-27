#!/usr/bin/env bash
# Mac / Linux: open the Subtitle Maker window.
# Linux also needs GTK's WebKit, e.g. on Ubuntu:  sudo apt install python3-gi gir1.2-webkit2-4.1
HERE="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${SUBTITLE_MAKER_HOME:-$HOME/.local/share/subtitle-maker}"
VPY="$HOME_DIR/venv/bin/python"
if [ ! -x "$VPY" ]; then
    echo "First time on this computer: run  bash \"$HERE/setup.sh\"  first."
    exit 1
fi
NV_LIBS="$("$VPY" -c 'import glob, os, nvidia; print(":".join(glob.glob(os.path.join(list(nvidia.__path__)[0], "*", "lib"))))' 2>/dev/null)"
[ -n "$NV_LIBS" ] && export LD_LIBRARY_PATH="$NV_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$VPY" "$HERE/app.py" "$@"
