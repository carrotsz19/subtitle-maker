"""Windows touches for the app window: progress on its taskbar button, flashing it, and a
notification when the queue is done. Everything here is best effort: on any problem (or on
another OS) it quietly does nothing, since none of it is needed to make subtitles.
"""

import queue
import sys
import threading
import uuid

WINDOWS = sys.platform == "win32"

# ITaskbarList3 states
NO_PROGRESS, INDETERMINATE, NORMAL, ERROR = 0, 1, 2, 4


def ui_thread(window, fn):
    """Run fn on the window's own thread (WinForms and its COM objects want that), without waiting."""
    from System import Action

    form = window.native
    form.BeginInvoke(Action(fn))


_HWNDS = {}


def hwnd_of(window):
    """The window's handle, read on its own thread (WinForms may refuse from another one)."""
    if id(window) not in _HWNDS:
        from System import Action

        form, found = window.native, []
        read = lambda: found.append(int(form.Handle.ToInt64()))  # noqa: E731
        form.Invoke(Action(read)) if form.InvokeRequired else read()
        _HWNDS[id(window)] = found[0]
    return _HWNDS[id(window)]


class Taskbar:
    """The progress bar drawn over the app's taskbar button. Call set() from any thread.

    The taskbar's COM object only works from a single-threaded COM apartment, and the window's
    own thread isn't one under pywebview ("No such interface supported"), so a small thread of
    its own makes every call.
    """

    def __init__(self, window):
        self._window = window
        self._last = None
        self._requests = queue.Queue()
        self.error = None
        self.ok = False  # a call has gone through (the Windows tests check this)
        threading.Thread(target=self._run, daemon=True).start()

    def _connect(self):
        import ctypes
        from ctypes import wintypes

        clsid = ctypes.create_string_buffer(uuid.UUID("56FDF344-FD6D-11d0-958A-006097C9A090").bytes_le, 16)
        iid = ctypes.create_string_buffer(uuid.UUID("ea1afb91-9e28-4b86-90e9-9e9f8a5eee6d").bytes_le, 16)
        ptr = ctypes.c_void_p()
        ctypes.oledll.ole32.CoCreateInstance(clsid, None, 1, iid, ctypes.byref(ptr))  # CLSCTX_INPROC_SERVER
        table = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))

        def method(index, *args):
            return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *args)(table[index])

        method(3)(ptr)  # HrInit
        value = method(9, wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)  # SetProgressValue
        state = method(10, wintypes.HWND, ctypes.c_int)  # SetProgressState
        hwnd = hwnd_of(self._window)
        return lambda done: value(ptr, hwnd, int(done * 1000), 1000), lambda s: state(ptr, hwnd, s)

    def _run(self):
        import ctypes

        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
            set_value, set_state = self._connect()
        except Exception as e:  # an old Windows, a locked-down PC: just no taskbar progress
            self.error = f"{e.__class__.__name__}: {e}"
            return
        while True:
            fraction, state = self._requests.get()
            try:
                if fraction is not None:
                    set_state(NORMAL)
                    set_value(max(0.0, min(1.0, fraction)))
                else:
                    set_state(state if state is not None else NO_PROGRESS)
                self.ok = True
            except Exception as e:
                self.error = f"{e.__class__.__name__}: {e}"
                return

    def set(self, fraction=None, state=None):
        """fraction 0-1 shows progress; state=INDETERMINATE, ERROR or NO_PROGRESS instead."""
        if not WINDOWS or self.error:
            return
        wanted = (None if fraction is None else round(fraction, 3), state)
        if wanted != self._last:
            self._last = wanted
            self._requests.put(wanted)


def in_background(window):
    """True when the window is minimised or another window has the focus."""
    if not WINDOWS:
        return False
    import ctypes

    try:
        hwnd = hwnd_of(window)
        return bool(ctypes.windll.user32.IsIconic(hwnd)) or ctypes.windll.user32.GetForegroundWindow() != hwnd
    except Exception:
        return False


def flash(window):
    """Flash the taskbar button until the window is brought back."""
    import ctypes
    from ctypes import wintypes

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]

    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd_of(window), 0x2 | 0xC, 0, 0)  # tray, until in front
    ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))


def notify(window, icon_path, title, text, on_click=None, done=None):
    """A Windows notification (a tray balloon, shown as a toast on Windows 10 and 11).

    done(error or None) is called once it has been shown, or has failed to show.
    """
    def show():
        try:
            from System.Drawing import Icon
            from System.Windows.Forms import NotifyIcon, ToolTipIcon

            tray = NotifyIcon()
            tray.Icon = Icon(str(icon_path))
            tray.Text = "Subtitle Maker"
            tray.Visible = True

            def clicked(sender, args):
                tray.Visible = False
                if on_click:
                    on_click()

            tray.BalloonTipClicked += clicked
            tray.Click += clicked
            tray.ShowBalloonTip(10000, title, text, ToolTipIcon.Info)
            # The tray icon is only there to carry the notification: take it away after a while
            threading.Timer(30, lambda: _quietly(ui_thread, window, tray.Dispose)).start()
            if done:
                done(None)
        except Exception as e:
            if done:
                done(f"{e.__class__.__name__}: {e}")

    try:
        ui_thread(window, show)
    except Exception as e:
        if done:
            done(f"{e.__class__.__name__}: {e}")


def _quietly(fn, *args):
    try:
        fn(*args)
    except Exception:  # the window has closed meanwhile
        pass


def bring_to_front(window):
    def restore():
        from System.Windows.Forms import FormWindowState

        form = window.native
        if form.WindowState == FormWindowState.Minimized:
            form.WindowState = FormWindowState.Normal
        form.Activate()

    try:
        ui_thread(window, restore)
    except Exception:
        pass
