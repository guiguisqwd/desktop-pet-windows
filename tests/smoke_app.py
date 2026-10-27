"""整个桌宠跑一遍（不开窗口，offscreen）：假装体态测试往 log.csv 里写行，看桌宠会不会提醒、看到改过来、给奖励、播奖励。
快速演示参数，大约 45 秒。用法：python tests/smoke_app.py   通过打印 ✓，没过打印 ✗ 并返回 1。"""
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="pet2-smoke-")
os.environ["PET2_DATA_DIR"] = os.path.join(TMP, "pet2")
os.environ["PET2_BACKEND_DIR"] = os.path.join(TMP, "体态测试数据")
os.environ["PET2_BACKEND_EXE"] = os.path.join(TMP, "没有这个后台.exe")   # 不启动后台：行由这里假装写
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, os.path.join(TMP, "settings"))
    os.makedirs(os.environ["PET2_DATA_DIR"])
    os.makedirs(os.environ["PET2_BACKEND_DIR"])
    with open(os.path.join(os.environ["PET2_DATA_DIR"], "onboarding.json"), "w", encoding="utf-8") as f:
        json.dump({"step": 9, "done": True, "selftest_reminded": False}, f)
    log_csv = os.path.join(os.environ["PET2_BACKEND_DIR"], "log.csv")
    with open(log_csv, "w", encoding="utf-8", newline="") as f:
        f.write("time,label,issues,severity,tilt_to_left_deg,persons\r\n")   # 后端的 csv 用 \r\n

    from pet2win.app.main import Host
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    QSettings("guigui", "BeibeijiaPet2Win").setValue("demo", True)
    host = Host(app)
    start = time.time()
    frames = {"n": 0}
    orig_frame = host._frame

    def counted_frame():
        frames["n"] += 1
        orig_frame()
    host._frame_timer.timeout.disconnect()
    host._frame_timer.timeout.connect(counted_frame)

    def write_row():
        t = time.time()
        age = t - start
        issues = "head_down" if age < 26 else ""
        with open(log_csv, "a", encoding="utf-8", newline="") as f:
            f.write(f"{t:.2f},normal,{issues},0.70,,1\r\n")

    writer = QTimer()
    writer.timeout.connect(write_row)
    writer.start(500)
    QTimer.singleShot(48_000, app.quit)
    t0 = time.time()
    app.exec()
    elapsed = time.time() - t0

    events_path = os.path.join(os.environ["PET2_DATA_DIR"], "events.jsonl")
    kinds = []
    with open(events_path, encoding="utf-8") as f:
        for line in f:
            kinds.append(json.loads(line)["kind"])
    need = ["app_started", "pet_reminder", "pet_saw_corrected", "reward_given", "reward_played", "app_quit"]
    missing = [k for k in need if k not in kinds]
    fps = frames["n"] / elapsed
    print(f"事件：{kinds}")
    print(f"画了 {frames['n']} 帧，平均 {fps:.0f} 帧/秒")
    shutil.rmtree(TMP, ignore_errors=True)
    if missing:
        print(f"✗ 少了这些事件：{missing}")
        return 1
    if fps < 20:
        print(f"✗ 太卡了：{fps:.0f} 帧/秒")
        return 1
    print("✓ 桌宠整个跑通：提醒 → 看到改过来 → 奖励 → 播奖励")
    return 0


if __name__ == "__main__":
    sys.exit(main())
