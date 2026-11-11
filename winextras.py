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


# ---- where the window was ----------------------------------------------------------------------
# Windows' own record of a window's place: its normal size and position (the one it goes back to
# from maximized), whether it's maximized, and so which screen it's on. Saved as it closes and put
# back before it first shows, so it opens exactly where it was, maximized or not, on the same
# monitor. pywebview's own x/y are converted with the first screen's scaling, which lands the
# window on the wrong monitor when the screens scale differently.

SW_HIDE, SW_SHOWMINIMIZED, SW_SHOWMAXIMIZED = 0, 2, 3
WPF_RESTORETOMAXIMIZED = 0x2


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
    """{"rect": [left, top, right, bottom], "maximized": bool} for the window, or None."""
    if not WINDOWS:
        return None
    import ctypes

    wp = _placement_struct()
    try:
        if not ctypes.windll.user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            return None
    except Exception:
        return None
    r = wp.rcNormalPosition
    # Minimized when closed: it reopens the way it was before (maximized if it was)
    maximized = wp.showCmd == SW_SHOWMAXIMIZED or (wp.showCmd == SW_SHOWMINIMIZED and bool(wp.flags & WPF_RESTORETOMAXIMIZED))
    return {"rect": [r.left, r.top, r.right, r.bottom], "maximized": maximized}


def set_placement(hwnd, saved, min_size=(200, 150)):
    """Put the window back where get_placement found it, without showing it. Only if that place is
    still on a screen that's connected now (a monitor may have been unplugged). True if it was."""
    if not WINDOWS or not isinstance(saved, dict):
        return False
    import ctypes
    from ctypes import wintypes

    try:
        left, top, right, bottom = (int(v) for v in saved["rect"])
    except (KeyError, TypeError, ValueError):
        return False
    if right - left < min_size[0] or bottom - top < min_size[1]:
        return False
    # Enough of the title bar has to be on a connected screen to grab it
    bar = wintypes.RECT(left + 40, top, right - 40, top + 30)
    try:
        if not ctypes.windll.user32.MonitorFromRect(ctypes.byref(bar), 0):  # MONITOR_DEFAULTTONULL
            return False
        wp = _placement_struct()
        wp.showCmd = SW_HIDE  # placed, still hidden: pywebview shows it next
        wp.rcNormalPosition = wintypes.RECT(left, top, right, bottom)
        return bool(ctypes.windll.user32.SetWindowPlacement(hwnd, ctypes.byref(wp)))
    except Exception:
        return False


# ---- limits on the worker (Settings > Hardware) --------------------------------------------------
# A Job Object holds the worker and everything it starts to a share of the processor (a hard cap)
# and a memory limit. Windows 8 and later; anywhere else (or if Windows refuses) there's no job, and
# the thread limit and priority still apply.

JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
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
    """The limits one worker runs under. memory_hit() says whether it went over its memory limit."""

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
        self.port = None
        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        # Windows posts "over the memory limit" to a completion port: that's how memory_hit() knows
        self.port = k32.CreateIoCompletionPort(wintypes.HANDLE(-1), None, None, 1)
        if self.port:
            self._set(JobObjectAssociateCompletionPortInformation, self._structs[3](None, self.port))
        if memory_mb:
            info = self._structs[1]()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = int(memory_mb) * 2**20
            self._set(JobObjectExtendedLimitInformation, info)
        if cpu_percent:
            # CpuRate is in hundredths of a percent of the whole processor (all its threads)
            self._set(JobObjectCpuRateControlInformation, self._structs[2](
                JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP, int(cpu_percent * 100)))
        # Read them back: a limit that didn't take must not look as if it had
        if memory_mb and self.memory_limit_mb() != int(memory_mb):
            raise OSError(f"the memory limit reads back as {self.memory_limit_mb()} MB")
        if cpu_percent and self.cpu_rate() != cpu_percent:
            raise OSError(f"the processor limit reads back as {self.cpu_rate()}%")

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

    def memory_hit(self):
        """True once anything in the job has been refused memory for going over the limit."""
        if self.port and not self._hit:
            ctypes, wintypes = self._ctypes, self._wintypes
            message, key, overlapped = wintypes.DWORD(), ctypes.c_void_p(), ctypes.c_void_p()
            while self._k32.GetQueuedCompletionStatus(self.port, ctypes.byref(message), ctypes.byref(key),
                                                      ctypes.byref(overlapped), 0):
                if message.value == JOB_OBJECT_MSG_JOB_MEMORY_LIMIT:
                    self._hit = True
        return self._hit

    def close(self):
        """The job's processes have ended: let go of it."""
        for handle in (self.port, self.handle):
            if handle:
                self._k32.CloseHandle(handle)
        self.port = self.handle = None


def limit_job(process, cpu_percent=None, memory_mb=None):
    """Hold a just-started process (and what it starts) to a share of the processor and a memory
    limit. Returns (Job or None, why not). Nothing to limit, or not Windows: (None, None)."""
    if not (cpu_percent or memory_mb) or not WINDOWS:
        return None, None
    try:
        job = Job(cpu_percent, memory_mb)
        job.assign(process)
        return job, None
    except Exception as e:  # a PC where Windows won't: the video still runs, without this limit
        return None, f"{e.__class__.__name__}: {e}"


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
