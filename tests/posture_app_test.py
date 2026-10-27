"""测桌宠管后台的那一层（PostureApp）：用一个假的“体态测试”（tests/fake_backend.py）代替真的，
走一遍：没校准 → 校准窗口 → 后台跑起来 → 暂停（叫它停）→ 再开 → 导出 → 自检 → 已经有别人在跑。
Mac 和 Windows 上都能跑（Windows 上用真的“停”事件，Mac 上用 SIGTERM）。用法：python tests/posture_app_test.py"""
import os
import shutil
import stat
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="pet2-pa-")
DATA = os.path.join(TMP, "体态测试数据")
os.makedirs(DATA)
os.environ["PET2_BACKEND_DIR"] = DATA
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAKE = os.path.join(ROOT, "tests", "fake_backend.py")
if sys.platform == "win32":
    # Windows 不能直接执行 .py：做一个 .cmd 包一层。用真正的 python.exe（不是 venv 里那个会再开一个子进程的启动器），
    # 这样假后台的父进程就是 cmd.exe，桌宠看到的进程号对得上
    launcher = os.path.join(TMP, "PostureTest.cmd")
    with open(launcher, "w", encoding="utf-8") as f:
        f.write(f'@echo off\r\nset PET2_FAKE_VIA_CMD=1\r\n"{getattr(sys, '_base_executable', sys.executable)}" "{FAKE}" %*\r\n')
else:
    launcher = os.path.join(TMP, "PostureTest")
    with open(launcher, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    os.chmod(launcher, os.stat(launcher).st_mode | stat.S_IEXEC)
os.environ["PET2_BACKEND_EXE"] = launcher

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer   # noqa: E402

from pet2win.app.posture_app import PostureApp                    # noqa: E402

failures = []


def check(cond, msg):
    print(("✓ " if cond else "✗ ") + msg, flush=True)
    if not cond:
        failures.append(msg)


def spin(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 50)
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until is not None else True


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    app = QCoreApplication(sys.argv[:1])   # noqa: F841
    pa = PostureApp()
    events = []
    pa.on_event = lambda kind, details: events.append((kind, details))
    finished = []
    pa.on_once_finished = lambda mode, code: finished.append((mode, code))

    pa.start_background()
    check(pa.state.kind == "needs_calibration", f"没校准：提示先校准（{pa.state}）")
    check(pa.status_text == "还没校准，点菜单里的“校准…”", "脚下那行字说先校准")

    pa.run_once("calibrate", "校准")
    check(pa.state.kind == "foreground" and pa.status_text == "校准中…", "校准窗口开着")
    spin(10, lambda: finished)
    check(finished == [("calibrate", 0)], f"校准窗口关了，退出码 0：{finished}")
    check(pa.is_calibrated, "校准好了（baseline.json 有了）")
    check(spin(10, lambda: pa.state.kind == "running"), f"校准完自动回到后台判断（{pa.state}）")

    pid_file = os.path.join(DATA, ".running.pid")
    check(spin(10, lambda: os.path.exists(pid_file) and open(pid_file).read().strip() != ""), "后台写了 .running.pid")

    done = []
    pa.stop_background(lambda: done.append(True))
    check(spin(10, lambda: done), "暂停：叫后台停，它自己退了")
    check(pa.state.kind == "stopped", f"状态是停了（{pa.state}）")
    exited = [d for k, d in events if k == "posture_app_exited"]
    check(bool(exited) and exited[-1].get("code") == "0", f"退出码 0（存好数据才退）：{exited[-1:] }")
    with open(os.path.join(DATA, "fake.log"), encoding="utf-8") as f:
        fake_log = f.read()
    check("stopped gracefully" in fake_log, "假后台收到的是“请停”，不是被强关")

    pa.start_background()
    check(spin(10, lambda: pa.state.kind == "running"), "恢复：后台又开起来了")

    results = []
    pa.export(True, False, lambda r: results.append(r))
    check(spin(15, lambda: results), "导出有结果")
    check(results and results[0][0] == "ok" and results[0][1].endswith(".zip"), f"导出成功，拿到 zip 路径：{results}")

    checks = []
    pa.check(lambda code, line: checks.append((code, line)))
    check(spin(15, lambda: checks), "自检有结果")
    check(checks and checks[0][0] == 0 and checks[0][1].startswith("OK"), f"自检：{checks}")

    pa.stop_synchronously()
    check(pa._process is None or pa._process.state() == pa._process.ProcessState.NotRunning, "退出桌宠时把后台停掉了")

    # 已经有别人在跑：.running.pid 里是一个活着的体态测试 → 不去撞它，等它关
    if sys.platform != "win32":   # Windows 上还要核对程序名，假后台对不上，这一项在 windows_backend_test 里测
        with open(pid_file, "w") as f:
            f.write(f"{os.getpid()}\n")
        pa2 = PostureApp()
        pa2.start_background()
        check(pa2.state.kind == "already_running", f"已经有一份在跑：只读它的数据，不去撞（{pa2.state}）")
        pa2.stop_background()
    shutil.rmtree(TMP, ignore_errors=True)
    if failures:
        print(f"✗ PostureApp 测试没过：{len(failures)} 项")
        return 1
    print("✓ PostureApp 测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
