"""假的“体态测试”，给 tests/posture_app_test.py 用：参数和退出码照真的来，但不开摄像头。
--no-preview：写 .running.pid，一直跑，收到“请停”（Windows 的事件 / SIGTERM）就清掉 pid、退出 0
--calibrate --once：等一秒，写 baseline.json，退出 0
--export：打一个 zip，最后一行打路径
--check：打一行 OK"""
import json
import os
import signal
import sys
import threading
import time
import zipfile


def main():
    args = sys.argv[1:]
    data = args[args.index("--data-dir") + 1] if "--data-dir" in args else os.path.expanduser("~/体态测试数据")
    os.makedirs(data, exist_ok=True)
    log = open(os.path.join(data, "fake.log"), "a", encoding="utf-8")

    def say(s):
        log.write(s + "\n")
        log.flush()

    if "--check" in args:
        print("OK model=fake chinese_font=yes")
        return 0
    if "--export" in args:
        out = os.path.join(data, "导出")
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "posture-data-00000000-20261027-000000.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("posture-data-00000000-20261027-000000/manifest.json", "{}")
        print("疼痛、拉扯分数：带上了；7 个点的坐标：没带" if "--with-pain" in args else "没带")
        print(path)
        return 0
    if "--calibrate" in args:
        time.sleep(1)
        with open(os.path.join(data, "baseline.json"), "w") as f:
            json.dump({"_time": int(time.time())}, f)
        say("calibrated")
        return 0
    if "--no-preview" in args:
        stop = threading.Event()
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
            k32.CreateEventW.restype = wintypes.HANDLE
            k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.WaitForSingleObject.restype = wintypes.DWORD
            # 测试在 Windows 上通过一个 .cmd 启动它：桌宠看到的进程号是 cmd.exe 的，所以按父进程的号等事件
            owner = os.getppid() if os.environ.get("PET2_FAKE_VIA_CMD") else os.getpid()
            h = k32.CreateEventW(None, True, False, f"Local\\BeibeijiaPostureStop_{owner}")
            threading.Thread(target=lambda: (k32.WaitForSingleObject(h, 0xFFFFFFFF), stop.set()), daemon=True).start()
        else:
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
        pid_path = os.path.join(data, ".running.pid")
        with open(pid_path, "w") as f:
            f.write(f"{os.getpid()}\n")
        while not stop.wait(0.1):
            pass
        with open(pid_path, "w") as f:
            f.write("")
        say("stopped gracefully")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
