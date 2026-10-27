"""把引导的 9 步、“我的数据”、“改勾选”、导出、删除都点一遍（不开窗口，offscreen；弹窗自动回答），看有没有哪里出错。
后台用 tests/fake_backend.py 假装。用法：python tests/ui_walk_test.py"""
import json
import os
import shutil
import stat
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="pet2-ui-")
os.environ["PET2_DATA_DIR"] = os.path.join(TMP, "pet2")
os.environ["PET2_BACKEND_DIR"] = os.path.join(TMP, "体态测试数据")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.makedirs(os.environ["PET2_BACKEND_DIR"])
FAKE = os.path.join(ROOT, "tests", "fake_backend.py")
if sys.platform == "win32":
    launcher = os.path.join(TMP, "PostureTest.cmd")
    with open(launcher, "w", encoding="utf-8") as f:
        f.write(f'@echo off\r\nset PET2_FAKE_VIA_CMD=1\r\n"{sys.executable}" "{FAKE}" %*\r\n')
else:
    launcher = os.path.join(TMP, "PostureTest")
    with open(launcher, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    os.chmod(launcher, os.stat(launcher).st_mode | stat.S_IEXEC)
os.environ["PET2_BACKEND_EXE"] = launcher

from PySide6.QtCore import QCoreApplication, QEventLoop          # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                # noqa: E402

from pet2win.app import form_kit as F                              # noqa: E402
from pet2win.app import my_data as MD                              # noqa: E402
from pet2win.app import platform_win                               # noqa: E402
from pet2win.app.pet_canvas import CanvasState                     # noqa: E402
from pet2win.app.posture_app import PostureApp                     # noqa: E402
from pet2win.app.resources import assets_dir                       # noqa: E402
from pet2win.core.onboarding import Step                           # noqa: E402

failures = []
answers = []        # 弹窗依次怎么答（按钮的下标）；没有就答第一个
alerts_seen = []


def fake_alert(title, body="", buttons=("好",), parent=None):
    alerts_seen.append(title)
    return answers.pop(0) if answers else 0


F.alert = fake_alert
MD.F.alert = fake_alert
platform_win.reveal = lambda path: None
platform_win.open_path = lambda path: None


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
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    pa = PostureApp()
    st = CanvasState(assets_dir())
    pet = QWidget()
    pet.setGeometry(1200, 600, 270, 390)
    log = []
    md = MD.MyData(pa, st, lambda: pet, lambda k, d: log.append((k, d)))

    # ---- 引导 ----
    md.start_if_needed()
    c = md.onboarding
    check(c is not None and c.is_active and c.state.step == Step.WELCOME, "第一次打开：引导从“欢迎”开始")
    c.next_button.click()
    check(c.state.step == Step.CONSENT and not c.next_button.isEnabled(), "第 2 步同意：没答年龄时“下一步”是灰的")
    form = c.consent_form
    form.adult_yes.click()
    form.boxes[1].click()   # ② 研究
    form.boxes[2].click()   # ③ 健康
    check(c.next_button.isEnabled() and form.boxes[2].isChecked(), "答了满 18、勾了 ②③ 以后能下一步")
    form.boxes[1].click()   # 取消 ② → ③ 跟着取消、变灰
    check(not form.boxes[2].isChecked() and not form.boxes[2].isEnabled(), "② 取消后 ③ 跟着取消、变灰")
    form.boxes[1].click()
    form.boxes[2].click()
    c.next_button.click()
    rec = md.data.consent
    check(c.state.step == Step.PROFILE and rec.current is not None and rec.current.c2 and rec.current.c3, "同意存好了（②③）")
    check(any(k == "consent_saved" for k, _ in log), "记了 consent_saved")
    c.next_button.click()   # 什么都不填：年龄空着也行（等于没填）
    check(c.state.step == Step.PLACEMENT and md.data.profile is not None, "第 3 步资料：空着也能存")
    check(spin(15, lambda: c.next_button.isEnabled()), "第 4 步摆放：体态测试自检通过后能下一步")
    c.next_button.click()
    check(c.state.step == Step.CALIBRATE and md.data.setup is not None, "摆放信息存了，到第 5 步校准")
    start = [b for b in c.body_widget.findChildren(type(c.next_button)) if b.text() == "开始校准"]
    check(len(start) == 1, "有“开始校准”按钮")
    if start:
        start[0].click()
        check(spin(15, lambda: c.next_button.isEnabled()), "校准完成后能下一步")
        check(len(md.data.calibration_files) == 1, "校准留档了一份（calibrations/）")
    c.next_button.click()
    check(c.state.step == Step.DIRECTION, "第 6 步方向检查")
    c.skip_button.setVisible(True)
    c._go(Step.MEET_PET)
    check(c.state.step == Step.MEET_PET and c.skip_button.isVisible(), "第 7 步认识桌宠可以跳过")
    c.skip_button.click()
    check(c.state.step == Step.SELFTEST and c.skip_button.text() == "以后再做", "第 8 步摸底测试：按钮是“以后再做”")
    c.skip_button.click()
    check(c.state.step == Step.FINISH and md.data.onboarding.selftest_deferred_at is not None, "选了以后再做：记下时间")
    c.next_button.click()
    check(md.data.onboarding.done and not c.is_active, "完成：引导关了，onboarding.json 写了 done")
    pa.stop_background()

    # ---- 我的数据、改勾选 ----
    md.show_my_data()
    check(md.window is not None and md.window.isVisible(), "“我的数据”窗口开了")
    md.edit_consent()
    w = md.consent_window
    check(w is not None and w.isVisible(), "“改勾选”窗口开了")
    form = md._consent_form
    form.boxes[1].click()   # 撤回 ②
    save = [b for b in w.findChildren(type(c.next_button)) if b.text() == "保存"][0]
    save.click()
    check(md.data.consent.current.c2 is False and "已经撤回“用于研究”。" in alerts_seen, "撤回 ② 存好了，并提示把编号告诉 gui")

    # ---- 导出：没勾 ② 不让导出 ----
    answers[:] = [1]   # “算了”
    md.export()
    check("还不能导出给 gui" in alerts_seen and not md.exporting, "没勾 ② 不让导出")
    md.edit_consent()
    md._consent_form.boxes[1].click()   # 重新勾 ②
    check(not md._consent_form.boxes[2].isChecked(), "撤回 ② 时 ③ 也一起撤回了，重新勾 ② 时 ③ 要自己再勾")
    md._consent_form.boxes[2].click()   # 再勾 ③
    [b for b in md.consent_window.findChildren(type(c.next_button)) if b.text() == "保存"][0].click()
    answers[:] = [1]   # 疼痛分：带上
    md.export()
    check(md.exporting and st.overlay_status == "正在导出…", "导出中：脚下写“正在导出…”")
    check(spin(15, lambda: not md.exporting), "导出完了")
    done = [t for t in alerts_seen if t.startswith("导出好了")]
    check(bool(done), f"导出好了：{done}")
    logs = open(md.data.export_log_path, encoding="utf-8").read().strip().splitlines() if os.path.exists(md.data.export_log_path) else []
    check(len(logs) == 1 and json.loads(logs[0])["with_pain"] is True, "export-log.jsonl 记了一次、带了疼痛分")

    # ---- 删除我的数据 ----
    answers[:] = [1, 1]   # 删除… → 确定删除
    md.delete_all()
    check(spin(10, lambda: "删好了" in alerts_seen or "有些文件没删掉" in alerts_seen), "删除走完了")
    check("删好了" in alerts_seen, "删得掉")
    check(md.onboarding is not None and md.onboarding.is_active and md.onboarding.state.step == Step.WELCOME,
          "删完重新走引导")
    md.onboarding.close()
    pa.stop_synchronously()
    shutil.rmtree(TMP, ignore_errors=True)
    if failures:
        print(f"✗ 界面走查没过：{len(failures)} 项")
        return 1
    print("✓ 界面走查全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
