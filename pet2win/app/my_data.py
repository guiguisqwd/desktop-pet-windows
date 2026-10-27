"""照翻 Pet2/Mac/MyData.swift：首次引导 + “我的数据”菜单。宿主只在几处调用这里（启动、每帧的画面、读到的行、气泡、菜单）。
导出只有这一份：菜单“导出数据…”和“我的数据 → 导出给 gui”都走 export()。

Windows 上改了的说法：“访达”→“资源管理器”；“隔空投送”→“当面拷给 gui（U 盘、数据线）”；“这台 Mac”→“这台电脑”。
"""
import dataclasses
import os
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from pet2win.core import export_package as ExportPackage
from pet2win.core.brain import Calm, Reminder, ReminderKind, Reminding
from pet2win.core.consent import Consent
from pet2win.core.pet2_data import ExportLogEntry, Pet2Data
from pet2win.core.posture_input import PostureRow

from . import animator as A
from . import form_kit as F
from . import platform_win
from .onboarding_window import OnboardingController


def _home_short(path):
    home = os.path.expanduser("~")
    return path.replace(home, "~", 1) if path.startswith(home) else path


class MyData:
    def __init__(self, posture_app, canvas_state, pet_window, log):
        self.data = Pet2Data(Pet2Data.default_directory(), Pet2Data.default_backend_directory())
        self.posture_app = posture_app
        self.canvas = canvas_state
        self.pet_window = pet_window          # () -> QWidget 或 None
        self.log = log                        # (kind, details) -> None
        self.onboarding = None
        self.window = None
        self.consent_window = None
        self.exporting = False
        # 引导或者摸底测试气泡临时让桌宠演的画面；None = 照 Brain 的。
        self.scene_override = None
        self._reminder_bubble = False
        self._mirrored_cache = (0.0, False)

    @staticmethod
    def now():
        return time.time()

    @property
    def onboarding_active(self):
        """引导开着时，Brain 不收 log 行（不提醒、不占限额）。"""
        return self.onboarding is not None and self.onboarding.is_active

    @property
    def reward_seconds(self):
        return A.STRETCH_SECONDS + max(2.0, self.canvas.heart_duration) + 2.5

    def start_if_needed(self):
        """启动时：引导没走完就先走引导。"""
        if not self.data.onboarding.done:
            self.show_onboarding(restart=False)

    # ---------- 读到的行 ----------

    def adjust(self, rows):
        """方向检查发现摄像头是镜像的：左右对调后再交给 Brain。"""
        if not self._mirrored():
            return rows
        out = []
        for r in rows:
            r = r.copy()
            r.tilt_to_left = -r.tilt_to_left if r.tilt_to_left is not None else None
            r.turn_to_left = -r.turn_to_left if r.turn_to_left is not None else None
            r.left_shoulder_up = -r.left_shoulder_up if r.left_shoulder_up is not None else None
            out.append(r)
        return out

    def _mirrored(self):
        if time.time() - self._mirrored_cache[0] > 5:
            setup = self.data.setup
            self._mirrored_cache = (time.time(), setup is not None and setup.mirrored is True)
        return self._mirrored_cache[1]

    # ---------- 每秒 ----------

    def tick(self, brain_scene):
        """摸底测试选了“以后再做”：24 小时后在气泡里问一次，只问一次。只在桌宠平常、没别的事时问。"""
        if self.onboarding_active or self.scene_override is not None or brain_scene != Calm():
            return
        s = self.data.onboarding
        if not s.should_remind_selftest(self.now()):
            return
        s.selftest_reminded = True
        self.data.save_onboarding(s)
        self._reminder_bubble = True
        self.scene_override = Reminding(Reminder(id=-2, kind=ReminderKind.POSTURE, issue=None, side=None,
                                                 line="昨天说好的摸底测试，现在做一下吗？大约 3 分钟。", started_at=self.now()))
        self.log("selftest_reminder_shown", {})

        def timeout():
            if self._reminder_bubble:
                self._clear_reminder("timeout")
        QTimer.singleShot(120_000, timeout)

    def bubble_menu(self):
        """气泡被点了：是摸底测试的气泡就给它的菜单，否则 None（交给 Brain 的）。"""
        if not self._reminder_bubble:
            return None
        m = QMenu()
        m.addAction("现在做", self._selftest_now)
        m.addAction("不用了", lambda: self._clear_reminder("declined"))
        return m

    def _selftest_now(self):
        self._clear_reminder("accepted")
        self.posture_app.run_once("selftest", "摸底测试")

    def _clear_reminder(self, how):
        self._reminder_bubble = False
        self.scene_override = None
        self.log("selftest_reminder_closed", {"how": how})

    # ---------- 菜单 ----------

    def add_menu(self, menu):
        """“我的数据”子菜单，放在宿主的菜单里。"""
        sub = menu.addMenu("我的数据")
        sub.addAction("我的数据…", self.show_my_data)
        sub.addAction("改勾选…", self.edit_consent)
        a = sub.addAction("正在导出…" if self.exporting else "导出给 gui…", self.export)
        a.setEnabled(not self.exporting and self.posture_app.executable is not None)
        sub.addSeparator()
        sub.addAction("重新走一遍第一次设置…" if self.data.onboarding.done else "继续第一次设置…",
                      lambda: self.show_onboarding(restart=self.data.onboarding.done))
        sub.addSeparator()
        sub.addAction("删除我的数据…", self.delete_all)

    def show_onboarding(self, restart):
        if self.onboarding_active:
            self.onboarding.show()
            return

        def pet_frame():
            w = self.pet_window()
            return w.frameGeometry() if w is not None else None

        def set_scene(scene):
            self.scene_override = scene

        def closed(done):
            self.scene_override = None
            if done:
                self.posture_app.start_background()

        c = OnboardingController(self.data, self.posture_app, {
            "log": self.log, "set_pet_scene": set_scene, "pet_frame": pet_frame,
            "reward_seconds": lambda: self.reward_seconds, "closed": closed,
        })
        if restart:
            c.restart()
        self.onboarding = c
        c.show()

    # ---------- 我的数据窗口 ----------

    def show_my_data(self):
        if self.window is None:
            self.window = QWidget(None, Qt.Window)
            self.window.setWindowTitle("我的数据")
        w = self.window
        old = w.layout()
        if old is not None:
            QWidget().setLayout(old)   # 换掉旧的内容
        c = self.data.consent.current
        mark = lambda on: "☑" if on else "☐"   # noqa: E731
        choices = "\n".join(f"{mark(on)} {label}" for label, on in zip(
            F.ConsentForm.labels, [c.c1, c.c2, c.c3, c.c4, c.c5, c.c6] if c else [None] * 6))
        pid = self.data.participant_id or "第一次导出后生成"
        stack = F.column([
            F.text(f"匿名编号：{pid}", size=14, weight=QFont.DemiBold),
            F.note("还没有同意记录。" if c is None else
                   f"同意书第 {c.version} 版，{'满 18 岁' if c.adult else '未满 18 岁'}，最后修改于 {self.date_text(c.time)}"),
            F.text(choices, size=12.5),
            F.text("数据放在这两个文件夹：", weight=QFont.DemiBold),
            F.note(_home_short(self.data.backend_directory) + "（体态测试写的）"),
            F.note(_home_short(self.data.directory) + "（桌宠写的）"),
            F.row([F.push("改勾选…", self.edit_consent), F.push("导出给 gui…", self.export),
                   F.push("在资源管理器里打开", self._open_folders), None]),
            F.row([F.push("删除我的数据…", self.delete_all), None]),
        ], spacing=10)
        layout = QVBoxLayout()
        layout.setContentsMargins(24, 22, 24, 22)
        layout.addWidget(stack)
        w.setLayout(layout)
        w.adjustSize()
        w.show()
        w.raise_()
        w.activateWindow()

    def _open_folders(self):
        platform_win.open_path(self.data.backend_directory)
        platform_win.open_path(self.data.directory)

    @staticmethod
    def date_text(t):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))

    # ---------- 改勾选 ----------

    def edit_consent(self):
        if self.consent_window is not None:
            self.consent_window.close()
        form = F.ConsentForm(self.data.consent.current)
        w = QWidget(None, Qt.Window)
        w.setWindowTitle("改勾选")
        save = F.push("保存", primary=True)
        cancel = F.push("取消", w.close)
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.addWidget(form.view)
        layout.addSpacing(16)
        layout.addWidget(F.row([None, cancel, save]))
        form.on_change = lambda: save.setEnabled(form.is_complete)
        save.setEnabled(form.is_complete)

        def on_save():
            choice = form.choice(self.now())
            if choice is None:
                return
            record = self.data.consent
            profile = self.data.profile
            withdrew = Consent.record(choice, profile.country if profile else None, record)
            self.data.save_consent(record)
            self.log("consent_changed", {"c2": "1" if record.current and record.current.c2 else "0",
                                         "withdrew_research": "1" if withdrew else "0"})
            w.close()
            if withdrew:
                pid = self.data.participant_id
                F.alert("已经撤回“用于研究”。",
                        "以后不能再导出给 gui。已经发给 gui 的数据，请把你的编号告诉 gui，他会删掉。" + (f"你的编号是 {pid}。" if pid else ""))
            if self.window is not None and self.window.isVisible():
                self.show_my_data()

        save.clicked.connect(on_save)
        self.consent_window = w
        self._consent_form = form
        w.adjustSize()
        w.show()
        w.raise_()
        w.activateWindow()

    # ---------- 导出给 gui ----------

    def _ask_include(self, text):
        """问一个“带不带”：默认是“不带”（回车就是不带）。问法照后端窗口。"""
        return F.alert(text, "", buttons=("不带", "带上")) == 1

    def export(self):
        """没勾 ② 不给导出；③ 勾了才问疼痛分，④ 勾了才问坐标；成功后往 zip 里追加 pet2/，写 export-log。"""
        if self.exporting:
            return
        consent = self.data.consent.current
        if not Consent.can_export(consent):
            r = F.alert("还不能导出给 gui", "要先在“改勾选”里勾上 ②（同意去标识数据用于研究），才能导出。不勾也不影响使用。",
                        buttons=("去改勾选", "算了"))
            if r == 0:
                self.edit_consent()
            return
        want_pain = Consent.asks_pain(consent) and self._ask_include(
            "带上自测时打的疼痛、拉扯分数吗？这些算健康信息，你同意才带。不带也能导出，角度照样带上")
        want_points = Consent.asks_points(consent) and self._ask_include(
            "带上 7 个点的坐标吗？鼻子、两眼、两耳、两肩在画面里的位置，只是数字，不是照片，gui 用它看你平时坐着的范围")
        args = Consent.export_arguments(consent, want_pain, want_points)
        if args is None:
            return
        with_pain, with_points = "--with-pain" in args, "--with-points" in args
        self.exporting = True
        self.canvas.overlay_status = "正在导出…"

        def done(result):
            self.exporting = False
            self.canvas.overlay_status = None
            if result[0] == "ok":
                path = result[1]
                note = ""
                try:
                    ExportPackage.append(ExportPackage.pet2_files(self.data, self.now()), path)
                except ExportPackage.ExportFailure as e:
                    note = f"\n\n（桌宠自己的资料没放进去：{e.message}）"
                    self.log("export_pet2_failed", {"reason": e.message})
                except Exception as e:   # noqa: BLE001 —— 别让一个意外把导出好的 zip 也弄没了
                    note = f"\n\n（桌宠自己的资料没放进去：{e}）"
                pid = self.data.participant_id
                self.data.append_export_log(ExportLogEntry(time=self.now(), zip=path, with_pain=with_pain,
                                                           with_points=with_points, participant_id=pid))
                platform_win.reveal(path)
                F.alert(f"导出好了：{os.path.basename(path)}",
                        "当面拷给 gui（U 盘、数据线都行），或者用他给的上传链接；不要用微信或群聊。"
                        + (f"\n你的匿名编号：{pid}" if pid else "") + note)
                if self.window is not None and self.window.isVisible():
                    self.show_my_data()
            else:
                F.alert("没导出", result[1])

        self.posture_app.export(with_pain, with_points, done)

    # ---------- 删除我的数据 ----------

    def delete_all(self):
        first = F.alert("删除我的数据？",
                        "会删掉这台电脑上的姿势记录、提醒记录、校准、摸底测试、导出的 zip，以及桌宠里的勾选、资料和记录。删了不能恢复。\n"
                        "已经发给 gui 的数据不在这里，要删请把编号告诉 gui。", buttons=("取消", "删除…"))
        if first != 1:
            return
        second = F.alert("真的删除吗？", "这一步不能撤销。", buttons=("取消", "确定删除"))
        if second != 1:
            return
        self.log("delete_requested", {})
        if self.onboarding is not None:
            self.onboarding.close()

        def after_stop():
            failed = self.data.delete_all()
            if self.window is not None:
                self.window.close()
            if not failed:
                F.alert("删好了", "下次打开会重新走第一次设置。")
            else:
                F.alert("有些文件没删掉", "\n".join(failed))
            self.show_onboarding(restart=True)

        self.posture_app.stop_background(after_stop)
