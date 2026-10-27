"""读别人正在写的文件。

Windows 上用 Python 自带的 open() 打开一个文件时，别的程序就不能给它改名或删掉它（少了 FILE_SHARE_DELETE）。
体态测试表头一改会把旧的 log.csv 改名成 log-old-…csv：要是桌宠正好在读，它就会改名失败、退出。
所以在 Windows 上读这些文件时用 CreateFileW 打开，三种共享都给。Mac / Linux 上就是普通的 open()。只用标准库。
"""
import os
import sys


def open_shared(path):
    """以只读二进制方式打开，允许别人同时写、改名、删除。打不开抛 OSError。"""
    if sys.platform != "win32":
        return open(path, "rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    FILE_SHARE_ALL = 0x1 | 0x2 | 0x4   # READ | WRITE | DELETE
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                       wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(os.fspath(path), GENERIC_READ, FILE_SHARE_ALL, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if handle == INVALID_HANDLE_VALUE or handle is None:
        err = ctypes.get_last_error()
        raise OSError(err, ctypes.FormatError(err), path)
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel32.CloseHandle(handle)
        raise
    return os.fdopen(fd, "rb")
