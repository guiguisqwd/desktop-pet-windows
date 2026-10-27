"""在 Windows 上测打包好的体态测试（PostureTest.exe）和桌宠接它的那几处：
1. --export：打出 zip，最后一行是 zip 的路径（窗口程序的输出也能交给管道）；桌宠再往里追加 pet2/。
2. 同一个数据文件夹只让一个在跑：第二个退出码 3（Mac 用 fcntl，Windows 换成了互斥量）。
3. 桌宠叫它停：点亮事件以后它自己退出、退出码 0（Mac 是 SIGTERM）。
4. 后台进程放进“作业”，桌宠一退它跟着退。
用法：python tests/windows_backend_test.py <PostureTest.exe 的路径>
CI 机器上没有摄像头：用 --camera 99（一个不存在的摄像头），它会试几次打开、每次停 1.5 秒，正好留出测试的时间。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pet2win.app import platform_win                              # noqa: E402
from pet2win.core import export_package                            # noqa: E402
from pet2win.core.consent import Consent, ConsentChoice, ConsentRecord  # noqa: E402
from pet2win.core.pet2_data import Pet2Data                        # noqa: E402

METRIC_KEYS = ["shoulder_width", "shoulder_mid_y", "neck_ratio", "shoulder_tilt_deg", "ear_span", "eye_span",
               "head_roll_deg", "head_turn", "head_pitch", "nose_angle", "ear_sh_l", "ear_sh_r"]
failures = []


def check(cond, msg):
    print(("✓ " if cond else "✗ ") + msg, flush=True)
    if not cond:
        failures.append(msg)


def wait_for(pred, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    exe = sys.argv[1]
    tmp = tempfile.mkdtemp(prefix="体态测试-")   # 路径里故意带中文
    data = os.path.join(tmp, "体态测试数据")
    os.makedirs(data)
    try:
        # ---- 1. 导出 ----
        with open(os.path.join(data, "log.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("time,label,issues,severity\r\n1793000000.00,normal,head_down,0.70\r\n")
        baseline = {k: 0.5 for k in METRIC_KEYS}
        baseline.update({"_raw": {"eye_y_px": 300.0, "ear_px": 90.0}, "_time": 1793000000})
        with open(os.path.join(data, "baseline.json"), "w", encoding="utf-8") as f:
            json.dump(baseline, f)
        res = subprocess.run([exe, "--export", "--data-dir", data], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=120, creationflags=platform_win.no_window_flags())
        out = res.stdout.decode("utf-8", errors="replace").strip().splitlines()
        print("   导出的输出：", out, flush=True)
        zip_path = out[-1].strip() if out else ""
        check(res.returncode == 0 and zip_path.endswith(".zip") and os.path.exists(zip_path), "--export 打出 zip，最后一行是路径")
        if os.path.exists(zip_path):
            mine = Pet2Data(os.path.join(tmp, "pet2"), data)
            record = ConsentRecord()
            Consent.record(ConsentChoice(time=1793000000, adult=True, c1=True, c2=True), "US", record)
            mine.save_consent(record)
            added = export_package.append(export_package.pet2_files(mine, time.time()), zip_path)
            names = zipfile.ZipFile(zip_path).namelist()
            top = os.path.splitext(os.path.basename(zip_path))[0]
            check(f"{top}/log.csv" in names and f"{top}/pet2/consent.json" in names and len(added) >= 2,
                  f"桌宠往 zip 的顶层文件夹里追加了 pet2/：{names}")

        # ---- 2. 锁：这里先拿住和后端同名的互斥量，再开一个，它应该说“已经在运行了”、退出码 3 ----
        import ctypes
        import hashlib
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        digest = hashlib.sha1(os.path.normcase(os.path.abspath(data)).encode("utf-8")).hexdigest()[:16]
        mutex = k32.CreateMutexW(None, False, f"Local\\BeibeijiaPostureLock_{digest}")
        check(bool(mutex) and k32.WaitForSingleObject(mutex, 0) == 0, "测试先拿住了数据文件夹的锁")
        args = [exe, "--no-preview", "--camera", "99", "--data-dir", data]
        p2 = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=120, creationflags=platform_win.no_window_flags())
        check(p2.returncode == 3, f"锁被别人拿着时，体态测试退出码是 3（实际 {p2.returncode}）")
        k32.ReleaseMutex(mutex)
        k32.CloseHandle(mutex)

        # ---- 3、4. 叫它停、作业 ----
        p1 = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              creationflags=platform_win.no_window_flags())
        job = platform_win.KillOnCloseJob()
        check(job.add(p1.pid), "后台进程放进了作业")
        pid_file = os.path.join(data, ".running.pid")

        def has_pid():
            try:
                with open(pid_file, encoding="utf-8") as f:
                    return f.read().strip() == str(p1.pid)
            except OSError:
                return False

        check(wait_for(has_pid, 90), ".running.pid 里写着它的进程号（拿到了锁）")
        check(platform_win.pid_alive(p1.pid, ("PostureTest.exe",)), "桌宠认得出它还在跑、是体态测试")
        check(not platform_win.pid_alive(p1.pid, ("别的程序.exe",)), "进程号对不上程序名时不算（防止进程号被重用）")
        still_running = p1.poll() is None
        check(still_running, "叫它停之前还在跑（还在试着打开摄像头）")
        if still_running:
            t = time.time()
            check(platform_win.request_stop(p1.pid), "点亮了“停”的事件")
            try:
                code = p1.wait(timeout=15)
            except subprocess.TimeoutExpired:
                code = None
                p1.kill()
            check(code == 0, f"叫它停以后自己退出、退出码 0（实际 {code}，用了 {time.time() - t:.1f} 秒）")
        log_text = ""
        try:
            with open(os.path.join(data, "运行记录.txt"), encoding="utf-8") as f:
                log_text = f.read()
        except OSError:
            pass
        print("   运行记录最后几行：", log_text.strip().splitlines()[-6:], flush=True)
        check("收到退出信号" in log_text, "运行记录里写了“收到退出信号”（和 Mac 收到 SIGTERM 一样处理）")
        check(wait_for(lambda: not has_pid(), 5), "退出时把 .running.pid 清空了")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"✗ Windows 后端测试没过：{len(failures)} 项", flush=True)
        return 1
    print("✓ Windows 后端测试全部通过", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
