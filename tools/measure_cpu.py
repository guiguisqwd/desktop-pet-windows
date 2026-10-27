"""量一下桌宠平常坐着时用多少 CPU（不开窗口，offscreen）。用法：python tools/measure_cpu.py [秒数]"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = tempfile.mkdtemp()
os.environ["PET2_DATA_DIR"] = os.path.join(TMP, "pet2")
os.environ["PET2_BACKEND_DIR"] = os.path.join(TMP, "b")
os.environ["PET2_BACKEND_EXE"] = os.path.join(TMP, "nope")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.makedirs(os.environ["PET2_DATA_DIR"])
os.makedirs(os.environ["PET2_BACKEND_DIR"])
with open(os.path.join(os.environ["PET2_DATA_DIR"], "onboarding.json"), "w") as f:
    json.dump({"step": 9, "done": True, "selftest_reminded": False}, f)
LOG = os.path.join(os.environ["PET2_BACKEND_DIR"], "log.csv")
with open(LOG, "w") as f:
    f.write("time,label,issues,severity,persons\r\n")

from PySide6.QtCore import QSettings, QTimer   # noqa: E402
from PySide6.QtWidgets import QApplication     # noqa: E402

QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, TMP)
from pet2win.app.main import Host               # noqa: E402

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20
app = QApplication(sys.argv[:1])
app.setQuitOnLastWindowClosed(False)
host = Host(app)


def row():
    with open(LOG, "a") as f:
        f.write(f"{time.time():.2f},normal,,0.0,1\r\n")


w = QTimer()
w.timeout.connect(row)
w.start(1000)
marks = {}
QTimer.singleShot(6000, lambda: marks.update(c0=time.process_time(), t0=time.time()))


def end():
    c = time.process_time() - marks["c0"]
    t = time.time() - marks["t0"]
    print(f"平常坐着：CPU {c / t * 100:.1f}% 一个核（{t:.0f} 秒里用了 {c:.2f} 秒）")
    app.quit()


QTimer.singleShot(int((6 + seconds) * 1000), end)
app.exec()
