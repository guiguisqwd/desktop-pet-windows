"""Windows 版“体态测试”的入口。posture_cam.py 是从 Mac 版原样复制来的（一个字不改，判断逻辑和 Mac 完全一样），
这里只在 Windows 上补几处 Mac 才有的东西，然后调它的 main()：

1. 同一个数据文件夹同一时间只让一个在跑：Mac 用 fcntl.flock，Windows 没有 fcntl，换成一个有名字的互斥量
   （.running.pid 照样写进程号，桌宠照样读）。
2. 中文字体：Mac 的字体路径在 Windows 上没有，加上 Windows 自带的微软雅黑、黑体、等线、宋体。
3. 弹窗、打开文件夹、在资源管理器里选中文件、相机隐私设置：Mac 用 osascript / open，这里用 Windows 的。
4. 窗口标题：Mac 用 Objective-C 把 OpenCV 窗口的英文标题改成中文，这里用 SetWindowTextW，顺便把窗口拿到最前面。
5. 桌宠叫它停：Mac 发 SIGTERM（它当成 Ctrl+C，存好数据再退）。Windows 没有 SIGTERM，桌宠点亮一个有名字的事件，
   这里等到了就给主线程送一个 Ctrl+C，后面的处理和 Mac 一模一样。
6. OpenCV 在 Windows 上打开摄像头有时要十几秒（MSMF 的硬件转换），关掉它。

同步 Mac 版时：把新的 posture_cam.py 原样复制过来就行；这里改的都是它的“外面”。如果 Mac 版改了这里用到的名字
（take_lock 里的 fcntl、FONT_FILES、alert、open_path、reveal、mac_title、CAMERA_SETTINGS），windows_check() 会报出来。
"""
import hashlib
import os
import sys
import threading
import types

IS_WINDOWS = sys.platform == "win32"

# 6. 必须在 import cv2 之前设
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.CreateEventW.restype = wintypes.HANDLE
    _u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _u32.FindWindowW.restype = wintypes.HWND
    _u32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    _u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]

    _held = []

    def _flock(f, operation):
        """fcntl.flock 的替身：只支持 LOCK_EX | LOCK_NB（take_lock 就用这个）。拿不到抛 OSError，和 fcntl 一样。"""
        folder = os.path.dirname(os.path.abspath(f.name))
        digest = hashlib.sha1(os.path.normcase(folder).encode("utf-8")).hexdigest()[:16]
        h = _k32.CreateMutexW(None, False, f"Local\\BeibeijiaPostureLock_{digest}")
        if not h:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        result = _k32.WaitForSingleObject(h, 0)
        if result in (0, 0x80):   # WAIT_OBJECT_0；WAIT_ABANDONED（上一个崩了没放）也算拿到
            _held.append(h)       # 一直拿着，进程退出时系统自动放开
            return
        _k32.CloseHandle(h)
        raise BlockingIOError(11, "已经有一个在跑")

    fcntl_stub = types.ModuleType("fcntl")
    fcntl_stub.LOCK_SH, fcntl_stub.LOCK_EX, fcntl_stub.LOCK_NB, fcntl_stub.LOCK_UN = 1, 2, 4, 8
    fcntl_stub.flock = _flock
    sys.modules.setdefault("fcntl", fcntl_stub)

import posture_cam as pc   # noqa: E402

WINDOWS_FONTS = ["msyh.ttc", "msyh.ttf", "msyhbd.ttc", "simhei.ttf", "Deng.ttf", "simsun.ttc"]


def _win_alert(msg):
    """弹一个对话框（Mac 的 osascript display dialog）。"""
    MB_OK, MB_ICONWARNING, MB_SETFOREGROUND, MB_TOPMOST = 0x0, 0x30, 0x10000, 0x40000
    try:
        _u32.MessageBoxW(None, str(msg), pc.APP_NAME, MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST)
    except OSError:
        pass


def _win_open(path):
    """打开文件夹，或者打开系统设置的某一页。"""
    try:
        os.startfile(path)   # noqa: S606
    except OSError:
        pass


def _win_reveal(path):
    """在资源管理器里打开文件所在的文件夹，并选中它。"""
    import subprocess
    try:
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
    except OSError:
        pass


def _win_title(win, title):
    """把 OpenCV 开的、标题是 win 的窗口改成 title（中文），并拿到最前面。改成了返回 True。"""
    try:
        hwnd = _u32.FindWindowW(None, win)
        if not hwnd:
            return False
        _u32.SetWindowTextW(hwnd, title)
        _u32.ShowWindow(hwnd, 5)   # SW_SHOW
        _u32.SetForegroundWindow(hwnd)
        return True
    except OSError:
        return False


def _watch_stop():
    """等桌宠点亮“停”的事件，等到了就给主线程送一个 Ctrl+C（和 Mac 上收到 SIGTERM 一样处理）。"""
    import _thread
    h = _k32.CreateEventW(None, True, False, f"Local\\BeibeijiaPostureStop_{os.getpid()}")
    if not h:
        return
    if _k32.WaitForSingleObject(h, 0xFFFFFFFF) == 0:
        _thread.interrupt_main()


def patch():
    if not IS_WINDOWS:
        return
    fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    pc.FONT_FILES.extend(os.path.join(fonts, name) for name in WINDOWS_FONTS)
    pc.alert = _win_alert
    pc.open_path = _win_open
    pc.reveal = _win_reveal
    pc.mac_title = _win_title
    pc.CAMERA_SETTINGS = "ms-settings:privacy-webcam"
    threading.Thread(target=_watch_stop, name="stop-event", daemon=True).start()


def windows_check():
    """posture_cam.py 换了新版本以后，这里用到的名字都还在吗。返回缺了的。"""
    import inspect
    missing = [name for name in ("FONT_FILES", "alert", "open_path", "reveal", "mac_title", "CAMERA_SETTINGS", "take_lock",
                                 "main", "APP_NAME") if not hasattr(pc, name)]
    if hasattr(pc, "take_lock") and "fcntl" not in inspect.getsource(pc.take_lock):
        missing.append("take_lock 不再用 fcntl")
    return missing


if __name__ == "__main__":
    patch()
    if "--windows-check" in sys.argv:
        gone = windows_check()
        print("OK windows patch" if not gone else f"FAIL 缺了：{gone}")
        sys.exit(0 if not gone else 1)
    sys.exit(pc.main())
