"""和系统打交道的几件事。Windows 上用 ctypes 调系统接口（不用装别的包）；在 Mac 上开发时给个能用的替代。

- 点穿：桌宠窗口透明的地方让鼠标点到后面的窗口（Mac 是 ignoresMouseEvents，这里是 WS_EX_TRANSPARENT）。
- 后台进程：放进一个“作业”，桌宠退出或崩了，体态测试跟着关，不会留下一个没人管、摄像头还亮着的进程。
- 叫后台停下：Windows 没有 SIGTERM，后端的入口（backend/posture_win.py）在等一个有名字的事件，桌宠把它点亮。
- 进程还在不在、是不是体态测试（Windows 上不能用 os.kill(pid, 0)：那会直接把进程关掉）。
- 摄像头权限：读注册表里“允许应用 / 桌面应用访问相机”的开关。
- 打开文件夹、在资源管理器里选中文件、打开系统设置的相机隐私页。
"""
import os
import signal
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_LAYERED = 0x00080000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _GetWindowLong = _user32.GetWindowLongPtrW
        _SetWindowLong = _user32.SetWindowLongPtrW
        _LONG = ctypes.c_longlong
    else:
        _GetWindowLong = _user32.GetWindowLongW
        _SetWindowLong = _user32.SetWindowLongW
        _LONG = ctypes.c_long
    _GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
    _GetWindowLong.restype = _LONG
    _SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, _LONG]
    _SetWindowLong.restype = _LONG
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                     ctypes.POINTER(wintypes.DWORD)]
    _kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.OpenEventW.restype = wintypes.HANDLE
    _kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001
    STILL_ACTIVE = 259
    EVENT_MODIFY_STATE = 0x0002
    ERROR_ALREADY_EXISTS = 183


# ---------- 点穿 ----------

def set_click_through(hwnd, on):
    """透明的地方让鼠标穿过去。返回现在的状态。只在 Windows 上有用。"""
    if not IS_WINDOWS or not hwnd:
        return False
    style = _GetWindowLong(hwnd, GWL_EXSTYLE)
    new = (style | WS_EX_TRANSPARENT | WS_EX_LAYERED) if on else (style & ~WS_EX_TRANSPARENT)
    if new != style:
        _SetWindowLong(hwnd, GWL_EXSTYLE, new)
    return on


def mouse_buttons_down():
    """有没有按着鼠标键（全局，不管鼠标在哪个窗口上）。"""
    if not IS_WINDOWS:
        return False
    return any(_user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x01, 0x02, 0x04))   # 左、右、中


def allow_foreground():
    """让接下来打开的校准、自测窗口能跑到最前面（Windows 默认不让别的程序抢前台）。"""
    if IS_WINDOWS:
        try:
            _user32.AllowSetForegroundWindow(0xFFFFFFFF)   # ASFW_ANY
        except OSError:
            pass


# ---------- 进程 ----------

def pid_alive(pid, image_names=None):
    """这个进程号还活着吗。image_names 给了就再看一眼是不是这个程序（防止进程号被别的程序重新用了）。"""
    if pid <= 0:
        return False
    if not IS_WINDOWS:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return False
        if image_names:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value).lower()
                return name in {n.lower() for n in image_names}
        return True
    finally:
        _kernel32.CloseHandle(h)


def stop_event_name(pid):
    """后端入口等着的那个事件的名字（backend/posture_win.py 里用同一个名字）。"""
    return f"Local\\BeibeijiaPostureStop_{pid}"


def request_stop(pid):
    """叫后台的体态测试停下（它会把还在跟的提醒记成 ended 再退出）。Windows：点亮事件；别的系统：SIGTERM。
    返回有没有送到。"""
    if pid is None or pid <= 0:
        return False
    if not IS_WINDOWS:
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            return False
    h = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, stop_event_name(pid))
    if not h:
        return False
    try:
        return bool(_kernel32.SetEvent(h))
    finally:
        _kernel32.CloseHandle(h)


class KillOnCloseJob:
    """Windows 的“作业”：放进来的进程在桌宠退出（包括崩溃）时被系统一起关掉。"""

    def __init__(self):
        self.handle = None
        if not IS_WINDOWS:
            return

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                          "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return
        info = EXTENDED()
        # KILL_ON_JOB_CLOSE | SILENT_BREAKAWAY_OK（后端再打开的资源管理器之类不算在作业里）
        info.BasicLimitInformation.LimitFlags = 0x2000 | 0x1000
        if not _kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            _kernel32.CloseHandle(job)
            return
        self.handle = job

    def add(self, pid):
        if not IS_WINDOWS or not self.handle or not pid:
            return False
        h = _kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not h:
            return False
        try:
            return bool(_kernel32.AssignProcessToJobObject(self.handle, h))
        finally:
            _kernel32.CloseHandle(h)


_instance_mutex = None


def single_instance(name="Local\\BeibeijiaPet2Win"):
    """同一时间只开一个桌宠。已经有一个在跑返回 False。"""
    global _instance_mutex
    if not IS_WINDOWS:
        return True
    h = _kernel32.CreateMutexW(None, False, name)
    if not h:
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(h)
        return False
    _instance_mutex = h
    return True


def no_window_flags():
    """subprocess 启动后端时不弹黑色的命令行窗口。"""
    return subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


# ---------- 摄像头权限 ----------

def camera_denied():
    """Windows 设置里把相机关掉了吗（“允许应用访问相机”或“允许桌面应用访问相机”）。读不到就当没关。"""
    if not IS_WINDOWS:
        return False
    import winreg
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
    checks = [(winreg.HKEY_LOCAL_MACHINE, base), (winreg.HKEY_CURRENT_USER, base),
              (winreg.HKEY_CURRENT_USER, base + r"\NonPackaged")]
    for root, path in checks:
        try:
            with winreg.OpenKey(root, path) as key:
                value, _ = winreg.QueryValueEx(key, "Value")
                if isinstance(value, str) and value.strip().lower() == "deny":
                    return True
        except OSError:
            continue
    return False


CAMERA_SETTINGS = "ms-settings:privacy-webcam"


def open_camera_settings():
    open_path(CAMERA_SETTINGS if IS_WINDOWS else "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera")


# ---------- 打开 ----------

def open_path(path):
    """打开文件夹、文件或系统设置页。"""
    try:
        if IS_WINDOWS:
            os.startfile(path)   # noqa: S606（只打开我们自己给的路径）
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def reveal(path):
    """在资源管理器里打开文件所在的文件夹，并选中它。"""
    try:
        if IS_WINDOWS:
            subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            open_path(os.path.dirname(path))
    except OSError:
        pass
