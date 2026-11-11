"""Windows touches for the app window: progress on its taskbar button, flashing it, and a
notification when the queue is done. Everything here is best effort: on any problem (or on
another OS) it quietly does nothing, since none of it is needed to make subtitles.
"""

import queue
import sys
import threading
import uuid

WINDOWS = sys.platform == "win32"

UNSUPPORTED = "not supported by this edition of Windows"

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
        self.step = "starting"
        self.ok = False  # a call has gone through (the Windows tests check this)
        threading.Thread(target=self._run, daemon=True).start()

    def _connect(self):
        import ctypes
        from ctypes import wintypes

        clsid = ctypes.create_string_buffer(uuid.UUID("56FDF344-FD6D-11d0-958A-006097C9A090").bytes_le, 16)
        iid = ctypes.create_string_buffer(uuid.UUID("ea1afb91-9e28-4b86-90e9-9e9f8a5eee6d").bytes_le, 16)
        ptr = ctypes.c_void_p()
        self.step = "creating the taskbar object"
        ctypes.oledll.ole32.CoCreateInstance(clsid, None, 1, iid, ctypes.byref(ptr))  # CLSCTX_INPROC_SERVER
        table = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))

        def method(index, *args):
            return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *args)(table[index])

        self.step = "HrInit"
        method(3)(ptr)  # HrInit
        value = method(9, wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)  # SetProgressValue
        state = method(10, wintypes.HWND, ctypes.c_int)  # SetProgressState
        self.step = "reading the window handle"
        hwnd = hwnd_of(self._window)
        self.step = "setting the progress"
        return lambda done: value(ptr, hwnd, int(done * 1000), 1000), lambda s: state(ptr, hwnd, s)

    def _run(self):
        import ctypes

        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
            set_value, set_state = self._connect()
        except OSError as e:
            # Some Windows editions (the Server ones GitHub tests on) only have the older taskbar
            # interfaces, without progress bars: E_NOINTERFACE
            self.error = (UNSUPPORTED if (e.winerror or 0) & 0xFFFFFFFF == 0x80004002
                          else f"{e.__class__.__name__}: {e} (while {self.step})")
            return
        except Exception as e:  # a locked-down PC: just no taskbar progress
            self.error = f"{e.__class__.__name__}: {e} (while {self.step})"
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
                self.error = f"{e.__class__.__name__}: {e} (while {self.step})"
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
    try:
        hwnd, user32 = hwnd_of(window), _win().user32
        return bool(user32.IsIconic(hwnd)) or user32.GetForegroundWindow() != hwnd
    except Exception:
        return False


def flash(window):
    """Flash the taskbar button until the window is brought back."""
    w = _win()
    info = w.FLASHWINFO(w.ctypes.sizeof(w.FLASHWINFO), hwnd_of(window), 0x2 | 0xC, 0, 0)  # tray, until in front
    w.user32.FlashWindowEx(w.ctypes.byref(info))


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


# ---- the Win32 calls used here ---------------------------------------------------------------
# Private copies of user32, comctl32 and dwmapi with every prototype set, made once. ctypes' shared
# windll ones convert arguments and results as C ints, which can cut a 64-bit handle or pointer
# short, and setting types on them would change them for pywebview's own calls too.

_WIN = []


def _win():
    if _WIN:
        return _WIN[0]
    import ctypes
    import types
    from ctypes import wintypes

    W = wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    lresult = ctypes.c_ssize_t
    subclassproc = ctypes.WINFUNCTYPE(lresult, W.HWND, W.UINT, W.WPARAM, W.LPARAM, ctypes.c_size_t, ctypes.c_size_t)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD)]

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", W.UINT), ("hwnd", W.HWND), ("dwFlags", W.DWORD), ("uCount", W.UINT), ("dwTimeout", W.DWORD)]

    def proto(dll, name, restype, *argtypes):
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = restype, argtypes

    rect_p, point_p = ctypes.POINTER(W.RECT), ctypes.POINTER(W.POINT)
    for name in ("IsZoomed", "IsIconic", "IsWindow"):
        proto(user32, name, W.BOOL, W.HWND)
    proto(user32, "GetForegroundWindow", W.HWND)
    proto(user32, "FlashWindowEx", W.BOOL, ctypes.c_void_p)
    proto(user32, "GetWindowPlacement", W.BOOL, W.HWND, ctypes.c_void_p)
    proto(user32, "SetWindowPlacement", W.BOOL, W.HWND, ctypes.c_void_p)
    proto(user32, "MonitorFromRect", W.HMONITOR, rect_p, W.DWORD)
    proto(user32, "GetMonitorInfoW", W.BOOL, W.HMONITOR, ctypes.c_void_p)
    proto(user32, "GetDpiForWindow", W.UINT, W.HWND)
    proto(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
    proto(user32, "GetSystemMetricsForDpi", ctypes.c_int, ctypes.c_int, W.UINT)
    proto(user32, "GetWindowLongW", W.LONG, W.HWND, ctypes.c_int)  # GWL_STYLE is a LONG, not pointer-sized
    proto(user32, "GetWindowRect", W.BOOL, W.HWND, rect_p)
    proto(user32, "GetCursorPos", W.BOOL, point_p)
    proto(user32, "GetAsyncKeyState", W.SHORT, ctypes.c_int)
    proto(user32, "ReleaseCapture", W.BOOL)
    proto(user32, "PostMessageW", W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    proto(user32, "SetWindowPos", W.BOOL, W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT)
    proto(comctl32, "SetWindowSubclass", W.BOOL, W.HWND, subclassproc, ctypes.c_size_t, ctypes.c_size_t)
    proto(comctl32, "RemoveWindowSubclass", W.BOOL, W.HWND, subclassproc, ctypes.c_size_t)
    proto(comctl32, "DefSubclassProc", lresult, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    proto(dwmapi, "DwmSetWindowAttribute", ctypes.c_long, W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD)  # an HRESULT
    _WIN.append(types.SimpleNamespace(ctypes=ctypes, wintypes=W, user32=user32, comctl32=comctl32, dwmapi=dwmapi,
                                      SUBCLASSPROC=subclassproc, MONITORINFO=MONITORINFO, FLASHWINFO=FLASHWINFO))
    return _WIN[0]


def _monitor_rects(hmonitor):
    """(rcMonitor, rcWork) of a monitor."""
    w = _win()
    info = w.MONITORINFO()
    info.cbSize = w.ctypes.sizeof(info)
    if not w.user32.GetMonitorInfoW(hmonitor, w.ctypes.byref(info)):
        raise OSError("no such monitor")
    return info.rcMonitor, info.rcWork


# ---- where the window was ----------------------------------------------------------------------
# Windows' own record of a window's place: its normal size and position (the one it goes back to
# from maximized), whether it's maximized, and so which screen it's on. Saved as it closes and put
# back before it first shows, so it opens exactly where it was, maximized or not, on the same
# monitor. pywebview's own x/y are converted with the first screen's scaling, which lands the
# window on the wrong monitor when the screens scale differently.

SW_HIDE, SW_SHOWMINIMIZED, SW_SHOWMAXIMIZED = 0, 2, 3
WPF_RESTORETOMAXIMIZED = 0x2
MONITOR_DEFAULTTONULL, MONITOR_DEFAULTTONEAREST = 0, 2


def _placement_struct():
    import ctypes
    from ctypes import wintypes

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                    ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                    ("rcNormalPosition", wintypes.RECT)]

    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    return wp


def get_placement(hwnd):
    """{"rect": [left, top, right, bottom], "maximized": bool} for the window, or None. The rect is
    in workspace coordinates, as Windows keeps it (see set_placement)."""
    if not WINDOWS:
        return None
    wp = _placement_struct()
    try:
        w = _win()
        if not w.user32.GetWindowPlacement(hwnd, w.ctypes.byref(wp)):
            return None
    except Exception:
        return None
    r = wp.rcNormalPosition
    # Minimized when closed: it reopens the way it was before (maximized if it was)
    maximized = wp.showCmd == SW_SHOWMAXIMIZED or (wp.showCmd == SW_SHOWMINIMIZED and bool(wp.flags & WPF_RESTORETOMAXIMIZED))
    return {"rect": [r.left, r.top, r.right, r.bottom], "maximized": maximized}


def set_placement(hwnd, saved, min_size=(200, 150), grip=(40, 40, 30)):
    """Put the window back where get_placement found it, without showing it. True if it was put
    somewhere: where it was if enough of it to grab is on a screen that's connected now, otherwise
    (a monitor unplugged, say) the same size, as far as it fits, on the nearest screen, so it
    still opens maximized if it was.

    grip is where the window is held to move it: (from its left, from its right, height) in pixels,
    the native title bar's by default, or the page's drag strip."""
    if not WINDOWS or not isinstance(saved, dict):
        return False
    try:
        left, top, right, bottom = (int(v) for v in saved["rect"])
    except (KeyError, TypeError, ValueError):
        return False
    if right - left < min_size[0] or bottom - top < min_size[1]:
        return False
    try:
        w = _win()
        RECT, byref = w.wintypes.RECT, w.ctypes.byref
        # The saved rect is in workspace coordinates: screen coordinates less the space a taskbar
        # docked at the top or left of the screen takes. MonitorFromRect wants screen ones; the
        # screen nearest the rect says how far apart the two are.
        near = w.user32.MonitorFromRect(byref(RECT(left, top, right, bottom)), MONITOR_DEFAULTTONEAREST)
        screen, work = _monitor_rects(near)
        dx, dy = work.left - screen.left, work.top - screen.top
        # Enough of the title bar (or the strip it's dragged by) has to be on a screen to grab it
        bar = RECT(left + dx + grip[0], top + dy, max(left + dx + grip[0] + 1, right + dx - grip[1]), top + dy + grip[2])
        if not w.user32.MonitorFromRect(byref(bar), MONITOR_DEFAULTTONULL):
            width, height = min(right - left, work.right - work.left), min(bottom - top, work.bottom - work.top)
            x = min(max(left + dx, work.left), work.right - width)
            y = min(max(top + dy, work.top), work.bottom - height)
            left, top, right, bottom = x - dx, y - dy, x - dx + width, y - dy + height
        wp = _placement_struct()
        wp.showCmd = SW_HIDE  # placed, still hidden: pywebview shows it next
        wp.rcNormalPosition = RECT(left, top, right, bottom)
        return bool(w.user32.SetWindowPlacement(hwnd, byref(wp)))
    except Exception:
        return False


# ---- the window's own title bar ----------------------------------------------------------------
# The page draws the title bar: a strip to drag the window by, and minimise, maximise and close.
# Only Windows' caption is taken away, not the frame. The window keeps its sizing frame and
# caption styles, so Windows itself still gives it what a window has: the shadow and rounded
# corners, Snap (dragging to an edge, Win+arrows), the system menu (Alt+Space), minimising and
# restoring from the taskbar button, and the animations of all of those. The caption's height is
# simply handed to the page (WM_NCCALCSIZE). The side and bottom sizing borders stay where Windows
# keeps them, invisible just outside the window, so resizing from those is Windows' own too. The
# top edge is inside the page, and no hit test reaches the window there (the web view's own window
# answers them all, even over its drag regions), so the page starts that one (begin_resize).
# pywebview's own frameless=True takes the whole frame away instead (FormBorderStyle None), and
# with it resizing, Snap, the animations and minimising from the taskbar.

WM_SIZE, WM_ACTIVATE, WM_NCDESTROY, WM_NCCALCSIZE = 0x5, 0x6, 0x82, 0x83
WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK, WM_SYSCOMMAND = 0xA1, 0xA3, 0x112
SIZE_MINIMIZED, SIZE_MAXIMIZED = 1, 2
SC_MINIMIZE, SC_MAXIMIZE, SC_CLOSE, SC_KEYMENU, SC_RESTORE = 0xF020, 0xF030, 0xF060, 0xF100, 0xF120
WS_CAPTION, WS_THICKFRAME, GWL_STYLE = 0x00C00000, 0x00040000, -16
SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_SWAPBUTTON, SM_CXPADDEDBORDER = 32, 33, 23, 92
# where a drag starting at the page's top edge resizes from (the hit-test codes Windows uses)
EDGES = {"n": 12, "nw": 13, "ne": 14}  # HTTOP, HTTOPLEFT, HTTOPRIGHT
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
SWP_FRAMECHANGED_ONLY = 0x20 | 0x1 | 0x2 | 0x4 | 0x10  # FRAMECHANGED | NOSIZE | NOMOVE | NOZORDER | NOACTIVATE
_SUBCLASS_ID = 0x53554254
_frames = {}  # hwnd: its CustomFrame, which holds the callback Windows calls
# Frames whose window has gone. Their callback can't be let go of from inside itself (the code
# Windows returns through would go with it), so the last few are kept here instead.
_retired = []


class CustomFrame:
    """Windows' caption taken off one window. on_state({"maximized", "minimized", "active"}) is
    called on the window's thread whenever one of those changes."""

    def __init__(self, hwnd, on_state=None):
        w = self._w = _win()
        self.hwnd, self.on_state, self._state = hwnd, on_state, {}
        style = w.user32.GetWindowLongW(hwnd, GWL_STYLE)
        if style & (WS_CAPTION | WS_THICKFRAME) != WS_CAPTION | WS_THICKFRAME:
            raise OSError("the window has no sizing frame and caption to keep")
        self._proc = w.SUBCLASSPROC(self._window_proc)  # held here: Windows calls it for as long as the window is there
        if not w.comctl32.SetWindowSubclass(hwnd, self._proc, _SUBCLASS_ID, 0):
            raise w.ctypes.WinError(w.ctypes.get_last_error())
        _frames[hwnd] = self
        # Windows 11 rounds the corners of a window with a frame anyway; this asks for it in so many
        # words. Earlier Windows say no (an error result), and keep square corners.
        w.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, w.ctypes.byref(w.ctypes.c_int(DWMWCP_ROUND)), 4)
        w.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FRAMECHANGED_ONLY)  # the frame worked out again, without the caption

    def remove(self):
        """Windows' own title bar back (on the window's thread)."""
        self.on_state = None
        self._w.comctl32.RemoveWindowSubclass(self.hwnd, self._proc, _SUBCLASS_ID)
        self._w.user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, SWP_FRAMECHANGED_ONLY)

    def _maximized_client(self, hwnd, rect, proposed):
        """The page's area when maximized: the window hangs over the screen's edges by its sizing
        border on every side, the way Windows maximizes every window, so the top keeps that much
        back (the sides and bottom already do); and nothing goes past the screen's work area, so
        a taskbar docked at any edge stays uncovered."""
        w = self._w
        dpi = w.user32.GetDpiForWindow(hwnd) or 96
        try:
            border = (w.user32.GetSystemMetricsForDpi(SM_CYSIZEFRAME, dpi)
                      + w.user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi))
        except AttributeError:  # before Windows 10 1607
            border = w.user32.GetSystemMetrics(SM_CYSIZEFRAME) + w.user32.GetSystemMetrics(SM_CXPADDEDBORDER)
        rect.top = proposed.top + border
        _, work = _monitor_rects(w.user32.MonitorFromRect(w.ctypes.byref(proposed), MONITOR_DEFAULTTONEAREST))
        rect.left, rect.top = max(rect.left, work.left), max(rect.top, work.top)
        rect.right, rect.bottom = min(rect.right, work.right), min(rect.bottom, work.bottom)

    def _window_proc(self, hwnd, msg, wparam, lparam, _id, _data):
        w = self._w
        default = w.comctl32.DefSubclassProc
        if msg == WM_NCCALCSIZE and wparam:
            try:
                rect = w.wintypes.RECT.from_address(lparam)  # the first of NCCALCSIZE_PARAMS' rectangles
                proposed = w.wintypes.RECT(rect.left, rect.top, rect.right, rect.bottom)  # the window's new rect
            except Exception:
                return default(hwnd, msg, wparam, lparam)
            result = default(hwnd, msg, wparam, lparam)  # takes off the borders and the caption
            try:
                if w.user32.IsZoomed(hwnd):
                    self._maximized_client(hwnd, rect, proposed)
                else:
                    rect.top = proposed.top  # the caption and top border go to the page
            except Exception:
                pass
            return result
        if msg == WM_SYSCOMMAND and wparam & 0xFFF0 == SC_KEYMENU and lparam != 0x20:
            # Alt (or F10) on its own, or Alt and a letter: Windows would go into the window menu's
            # keyboard mode, shown only by the caption that's no longer there (and beep for a
            # letter, there being no menu bar), and the next click or close would vanish into it.
            # Alt+Space (lparam ' ') still opens the window menu; Alt+F4 is SC_CLOSE, not this.
            return 0
        if msg == WM_NCDESTROY:
            self.on_state = None
            try:
                w.comctl32.RemoveWindowSubclass(hwnd, self._proc, _SUBCLASS_ID)
            except Exception:
                pass
            result = default(hwnd, msg, wparam, lparam)
            if _frames.get(hwnd) is self:
                _retired.append(_frames.pop(hwnd))
                del _retired[:-4]
            return result
        if msg in (WM_SIZE, WM_ACTIVATE) and self.on_state:
            try:
                self._changed(hwnd, msg, wparam)
            except Exception:
                pass
        return default(hwnd, msg, wparam, lparam)

    def _changed(self, hwnd, msg, wparam):
        user32 = self._w.user32
        state = dict(self._state)
        if msg == WM_SIZE:
            minimized = wparam == SIZE_MINIMIZED
            # minimized from maximized, it still is (it comes back maximized)
            state.update(maximized=wparam == SIZE_MAXIMIZED or (minimized and state.get("maximized", False)),
                         minimized=minimized)
        else:
            state["active"] = wparam & 0xFFFF != 0  # WA_INACTIVE is 0
        state.setdefault("maximized", bool(user32.IsZoomed(hwnd)))
        state.setdefault("minimized", bool(user32.IsIconic(hwnd)))
        state.setdefault("active", user32.GetForegroundWindow() == hwnd)
        if state != self._state:
            self._state = state
            self.on_state(dict(state))


def custom_frame(hwnd, on_state=None):
    """Take Windows' caption off the window (see above), on the window's own thread, before it
    shows. Returns None once it's off, or why not: then the window keeps its own title bar."""
    if not WINDOWS:
        return "not Windows"
    try:
        CustomFrame(hwnd, on_state)
        return None
    except Exception as e:
        return f"{e.__class__.__name__}: {e}"


def native_frame(hwnd):
    """Undo custom_frame: Windows' own title bar again (on the window's own thread)."""
    frame = _frames.pop(hwnd, None)
    if frame:
        frame.remove()
        _retired.append(frame)
        del _retired[:-4]


def dpi_scale(hwnd):
    """The window's pixels per page pixel (1.0 at 100%)."""
    try:
        return (_win().user32.GetDpiForWindow(hwnd) or 96) / 96
    except Exception:
        return 1.0


def window_state(hwnd):
    """{"maximized", "active"} right now."""
    user32 = _win().user32
    return {"maximized": bool(user32.IsZoomed(hwnd)), "active": user32.GetForegroundWindow() == hwnd}


def window_command(hwnd, command):
    """What the page's title bar buttons do, exactly as Windows' own would (so closing still asks
    first while a video is running, and the animations play): "minimise", "maximise" (restore
    when it's maximized) or "close"."""
    user32 = _win().user32
    if command == "maximise":
        sc = SC_RESTORE if user32.IsZoomed(hwnd) else SC_MAXIMIZE
    else:
        sc = {"minimise": SC_MINIMIZE, "close": SC_CLOSE}[command]
    return bool(user32.PostMessageW(hwnd, WM_SYSCOMMAND, sc, 0))


def begin_resize(hwnd, edge, at=None, double=False, min_size=(0, 0)):
    """The pointer went down on the page's top edge (at: where, in screen pixels): resize from it
    as if it had gone down on a native border. Run on the window's own thread.

    The page's message takes a moment to get here. While the button is still down, Windows' own
    sizing loop takes over, anchored where the pointer went down, so what it moved meanwhile
    counts. If the button is already up (a quick flick), the window takes the move it made. A
    second press (double) is Windows' double-click on a border: the window fills the screen's height."""
    w = _win()
    user32 = w.user32
    if edge not in EDGES or user32.IsZoomed(hwnd):
        return False
    now = w.wintypes.POINT()
    user32.GetCursorPos(w.ctypes.byref(now))
    x, y = now.x, now.y
    if at and abs(int(at[0]) - x) <= 200 and abs(int(at[1]) - y) <= 200:  # (a far-off one is a bad reading)
        x, y = int(at[0]), int(at[1])
    point = (x & 0xFFFF) | (y & 0xFFFF) << 16
    if double:
        return bool(user32.PostMessageW(hwnd, WM_NCLBUTTONDBLCLK, EDGES[edge], point))
    button = 0x02 if user32.GetSystemMetrics(SM_SWAPBUTTON) else 0x01  # VK_RBUTTON when swapped, else VK_LBUTTON
    if user32.GetAsyncKeyState(button) & 0x8000:
        user32.ReleaseCapture()  # the web view has the mouse; Windows' sizing loop takes it from here
        # Posted, as the mouse's own messages are: the loop then runs from the window's message loop,
        # not from inside this call
        return bool(user32.PostMessageW(hwnd, WM_NCLBUTTONDOWN, EDGES[edge], point))
    if (now.x, now.y) == (x, y):
        return False  # a click, not a drag
    r = w.wintypes.RECT()
    user32.GetWindowRect(hwnd, w.ctypes.byref(r))
    left, top, right = r.left, min(r.top + now.y - y, r.bottom - min_size[1]), r.right
    if edge == "nw":
        left = min(r.left + now.x - x, r.right - min_size[0])
    elif edge == "ne":
        right = max(r.right + now.x - x, r.left + min_size[0])
    return bool(user32.SetWindowPos(hwnd, None, left, top, right - left, r.bottom - top, 0x4 | 0x10))  # NOZORDER | NOACTIVATE


# ---- limits on the worker (Settings > Hardware) --------------------------------------------------
# A Job Object holds the worker and everything it starts to a share of the processor (a hard cap)
# and a memory limit. Windows 8 and later; anywhere else (or if Windows refuses) there's no job, and
# the thread limit and priority still apply.

JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x800, 0x2000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE, JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x1, 0x4
JOB_OBJECT_MSG_JOB_MEMORY_LIMIT = 10
JobObjectBasicAccountingInformation, JobObjectAssociateCompletionPortInformation = 1, 7
JobObjectExtendedLimitInformation, JobObjectCpuRateControlInformation = 9, 15


def _job_structs():
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class CpuRate(ctypes.Structure):
        _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

    class Port(ctypes.Structure):
        _fields_ = [("CompletionKey", ctypes.c_void_p), ("CompletionPort", wintypes.HANDLE)]

    class Accounting(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    return Basic, Extended, CpuRate, Port, Accounting


class Job:
    """The limits one worker runs under. new_hits() says whether it went over its memory limit
    since it was last asked. Each limit is applied on its own: one Windows refuses is left out
    (and said in problems), the other still holds. Everything in the job ends when it's closed."""

    def __init__(self, cpu_percent=None, memory_mb=None):
        import ctypes
        from ctypes import wintypes

        self._k32 = k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateIoCompletionPort.restype = wintypes.HANDLE
        k32.CreateIoCompletionPort.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
        k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                  ctypes.POINTER(wintypes.DWORD))
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k32.GetQueuedCompletionStatus.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                                  ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                                  wintypes.DWORD)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._ctypes, self._wintypes, self._structs = ctypes, wintypes, _job_structs()
        self.cpu_percent, self.memory_mb = cpu_percent, memory_mb
        self._hit = False
        self.problems = []
        self.port = None
        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self._apply(cpu_percent, memory_mb)
        except BaseException:
            self.close()  # nothing half made is left behind
            raise

    def _apply(self, cpu_percent, memory_mb):
        k32, wintypes = self._k32, self._wintypes
        # Windows posts "over the memory limit" to a completion port: that's how new_hits() knows
        self.port = k32.CreateIoCompletionPort(wintypes.HANDLE(-1), None, None, 1)
        if self.port:
            self._set(JobObjectAssociateCompletionPortInformation, self._structs[3](None, self.port))
        # Everything in the job ends with it (the app closing, or crashing, takes the whole worker
        # tree along), and nvidia-smi may leave it: it's started with CREATE_BREAKAWAY_FROM_JOB, so
        # its memory isn't counted against the limit
        base = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
        self._limit_flags(base)
        if memory_mb:
            try:
                self._limit_flags(base | JOB_OBJECT_LIMIT_JOB_MEMORY, memory_mb)
                if self.memory_limit_mb() != int(memory_mb):  # a limit that didn't take mustn't look as if it had
                    raise OSError(f"it reads back as {self.memory_limit_mb()} MB")
            except OSError as e:
                self.memory_mb = None
                self.problems.append(f"memory limit: {e}")
                self._limit_flags(base)
        if cpu_percent:
            try:
                # CpuRate is in hundredths of a percent of the whole processor (all its threads)
                self._set(JobObjectCpuRateControlInformation, self._structs[2](
                    JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP, int(cpu_percent * 100)))
                if self.cpu_rate() != cpu_percent:
                    raise OSError(f"it reads back as {self.cpu_rate()}%")
            except OSError as e:
                self.cpu_percent = None
                self.problems.append(f"processor limit: {e}")

    def _limit_flags(self, flags, memory_mb=None):
        info = self._structs[1]()
        info.BasicLimitInformation.LimitFlags = flags
        if memory_mb:
            info.JobMemoryLimit = int(memory_mb) * 2**20
        self._set(JobObjectExtendedLimitInformation, info)

    def _set(self, kind, struct):
        ctypes = self._ctypes
        if not self._k32.SetInformationJobObject(self.handle, kind, ctypes.byref(struct), ctypes.sizeof(struct)):
            raise ctypes.WinError(ctypes.get_last_error())

    def _query(self, kind, struct):
        ctypes = self._ctypes
        if not self._k32.QueryInformationJobObject(self.handle, kind, ctypes.byref(struct), ctypes.sizeof(struct), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return struct

    def assign(self, process):
        """Put a just-started subprocess.Popen in the job; what it starts afterwards joins it too."""
        if not self._k32.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def memory_limit_mb(self):
        info = self._query(JobObjectExtendedLimitInformation, self._structs[1]())
        return info.JobMemoryLimit // 2**20 if info.BasicLimitInformation.LimitFlags & JOB_OBJECT_LIMIT_JOB_MEMORY else None

    def peak_memory_mb(self):
        """The most memory the whole job has had committed at once."""
        return self._query(JobObjectExtendedLimitInformation, self._structs[1]()).PeakJobMemoryUsed / 2**20

    def cpu_rate(self):
        info = self._query(JobObjectCpuRateControlInformation, self._structs[2]())
        return info.CpuRate / 100 if info.ControlFlags & JOB_OBJECT_CPU_RATE_CONTROL_ENABLE else None

    def cpu_seconds(self):
        """Processor time the whole job has used (all its processes, ended ones too)."""
        info = self._query(JobObjectBasicAccountingInformation, self._structs[4]())
        return (info.TotalUserTime + info.TotalKernelTime) / 1e7

    def processes(self):
        """How many processes have been in the job (ended ones too)."""
        return self._query(JobObjectBasicAccountingInformation, self._structs[4]()).TotalProcesses

    def new_hits(self):
        """True if anything in the job was refused memory for going over the limit since the last
        call (the app asks at the start of each video, then when one stops)."""
        hit = False
        if self.port:
            ctypes, wintypes = self._ctypes, self._wintypes
            message, key, overlapped = wintypes.DWORD(), ctypes.c_void_p(), ctypes.c_void_p()
            while self._k32.GetQueuedCompletionStatus(self.port, ctypes.byref(message), ctypes.byref(key),
                                                      ctypes.byref(overlapped), 0):
                hit |= message.value == JOB_OBJECT_MSG_JOB_MEMORY_LIMIT
        return hit

    def close(self):
        """Let go of the job: anything still in it ends (KILL_ON_JOB_CLOSE)."""
        for handle in (self.port, self.handle):
            if handle:
                self._k32.CloseHandle(handle)
        self.port = self.handle = None


def limit_job(process, cpu_percent=None, memory_mb=None):
    """Hold a just-started process (and what it starts) to a share of the processor and a memory
    limit. Returns (Job or None, why not). Nothing to limit, or not Windows: (None, None)."""
    if not (cpu_percent or memory_mb) or not WINDOWS:
        return None, None
    job = None
    try:
        job = Job(cpu_percent, memory_mb)
        job.assign(process)
    except Exception as e:  # a PC where Windows won't: the video still runs, without these limits
        if job:
            job.close()
        return None, f"{e.__class__.__name__}: {e}"
    return job, "; ".join(job.problems) or None


def lower_priority(process, low=True):
    """Let other apps go first: below-normal priority (what it starts inherits it). Returns why
    not, or None."""
    if not low:
        return None
    try:
        import psutil

        psutil.Process(process.pid).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if WINDOWS else 10)
        return None
    except Exception as e:
        return f"{e.__class__.__name__}: {e}"
