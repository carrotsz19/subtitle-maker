"""The small window shown while an update installs new Python packages after the app has closed.

The app can't install them itself: Windows keeps the packages it has loaded locked until it
closes. This used to be a console window full of pip's output; now it's a small window in the
app's colours, over where the app was, saying what's happening. It uses tkinter, which comes
with Python, because the app's own window library (pywebview) may be one of the packages being
replaced. Without tkinter it installs with no window at all, rather than open a console.
"""

import subprocess
import sys
import threading
from pathlib import Path

from paths import dark_mode, data_dir, load_settings

HERE = Path(__file__).parent
LOG = data_dir() / "update-packages.log"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

LIGHT = {"bg": "#ffffff", "ink": "#1b1a18", "dim": "#6d6961", "track": "#eceae5", "accent": "#b3492b", "bad": "#b3492b"}
DARK = {"bg": "#1e1c17", "ink": "#f1ede6", "dim": "#a39d92", "track": "#2c2925", "accent": "#e0643f", "bad": "#ef7a5a"}


def console_python():
    """python.exe (not pythonw.exe), so pip's output can be read; it runs with no window."""
    exe = Path(sys.executable)
    return str(exe.with_name("python.exe")) if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists() else str(exe)


def run_pip(requirements, on_line=lambda line: None):
    """Install one requirements file with no console window. Raises CalledProcessError if it fails."""
    cmd = [console_python(), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(HERE / requirements)]
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(cmd)}\n")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", creationflags=NO_WINDOW, cwd=str(HERE))
        for line in proc.stdout:
            log.write(line)
            on_line(line.strip())
        if proc.wait():
            raise subprocess.CalledProcessError(proc.returncode, cmd)


def friendly(line):
    """pip's line, said briefly: "Downloading torch (2.4 GB)" and the like; None to keep the last one."""
    for start, say in (("Collecting ", "Checking "), ("Downloading ", "Downloading "), ("Installing collected packages", None),
                       ("Successfully installed", "Done")):
        if line.startswith(start):
            if say is None:
                return "Installing them"
            name = line[len(start):].split(" ", 1)[0].split("/")[-1].split("-")[0].split("==")[0].split(">")[0].split("<")[0]
            size = line[line.find("("):line.find(")") + 1] if "Downloading" in start and "(" in line else ""
            return f"{say}{name} {size}".strip() if say != "Done" else "Done"
    return None


def where_the_app_was():
    """The centre of the app window's last place on screen, if Windows told us."""
    placement = load_settings().get("placement") or {}
    try:
        left, top, right, bottom = (int(v) for v in placement["rect"])
        return (left + right) // 2, (top + bottom) // 2
    except (KeyError, TypeError, ValueError):
        return None


def install(pending):
    """Install the pending requirements files, showing progress. True if they all went in."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        try:
            for name in pending:
                run_pip(name)
            return True
        except (OSError, subprocess.CalledProcessError):
            return False

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled screens
        except Exception:
            pass
    c = DARK if dark_mode() else LIGHT
    root = tk.Tk()
    root.title("Subtitle Maker")
    root.configure(bg=c["bg"])
    root.resizable(False, False)
    try:
        root.iconbitmap(str(HERE / "ui" / "icon.ico"))
    except tk.TclError:
        pass
    scale = root.winfo_fpixels("1i") / 96
    width, height = int(440 * scale), int(158 * scale)
    centre = where_the_app_was() or (root.winfo_screenwidth() // 2, root.winfo_screenheight() // 2)
    root.geometry(f"{width}x{height}+{centre[0] - width // 2}+{centre[1] - height // 2}")

    font = "Segoe UI" if sys.platform == "win32" else "TkDefaultFont"
    pad = int(22 * scale)
    frame = tk.Frame(root, bg=c["bg"], padx=pad, pady=int(18 * scale))
    frame.pack(fill="both", expand=True)
    title = tk.Label(frame, text="Finishing the update", font=(font, 13, "bold"), bg=c["bg"], fg=c["ink"], anchor="w")
    title.pack(fill="x")
    text = tk.Label(frame, text="Adding the new parts it needs. This takes a minute or two, then Subtitle Maker opens again.",
                    font=(font, 9), bg=c["bg"], fg=c["dim"], anchor="w", justify="left", wraplength=width - 2 * pad)
    text.pack(fill="x", pady=(int(4 * scale), int(12 * scale)))
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Bar.Horizontal.TProgressbar", troughcolor=c["track"], background=c["accent"], bordercolor=c["track"],
                    lightcolor=c["accent"], darkcolor=c["accent"], thickness=max(4, int(5 * scale)))
    bar = ttk.Progressbar(frame, mode="indeterminate", style="Bar.Horizontal.TProgressbar")
    bar.pack(fill="x")
    bar.start(12)
    detail = tk.Label(frame, text="Starting", font=(font, 8), bg=c["bg"], fg=c["dim"], anchor="w")
    detail.pack(fill="x", pady=(int(8 * scale), 0))

    # Closing it mid-install would leave half the packages in: it stays until it's done
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    result = {}

    def say(line):
        shown = friendly(line)
        if shown:
            root.after(0, lambda: detail.config(text=shown[:70]))

    def work():
        try:
            for name in pending:
                run_pip(name, say)
            result["ok"] = True
        except (OSError, subprocess.CalledProcessError) as e:
            result["error"] = e
        root.after(0, done)

    def done():
        bar.stop()
        if result.get("ok"):
            root.destroy()
            return
        # It didn't work: say so in the same window, with the way to fix it
        title.config(text="The update couldn't finish")
        text.config(text="New parts it needs didn't install. Run Setup.bat in the Subtitle Maker folder to add them; "
                         "your settings and subtitles are kept.", fg=c["ink"])
        bar.pack_forget()
        detail.config(text=f"What pip said is in {LOG}", fg=c["dim"], wraplength=width - 2 * pad, justify="left")
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        tk.Button(frame, text="Close", command=root.destroy, font=(font, 9, "bold"), bg=c["accent"], fg="#ffffff",
                  activebackground=c["accent"], activeforeground="#ffffff", relief="flat", bd=0,
                  padx=int(16 * scale), pady=int(5 * scale), cursor="hand2").pack(anchor="e", pady=(int(10 * scale), 0))
        root.geometry(f"{width}x{int(214 * scale)}")

    threading.Thread(target=work, daemon=True).start()
    root.mainloop()
    return bool(result.get("ok"))
