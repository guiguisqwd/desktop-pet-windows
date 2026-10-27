"""照翻 Pet2/Mac/OnboardingWindow.swift：首次引导。一个小窗口一步一页，放在桌宠旁边。每步都能“上一步”；第 7、8 步可以跳过。
走到哪一步写进 onboarding.json，中途关掉下次接着走。规格见 Pet2/docs/首次引导和导出-规格.md。

Windows 上改了的只有说法：Mac → 电脑；系统设置的路径换成 Windows 的；没找到体态测试时让用户完整解压。
"""
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from pet2win.core.brain import Drifting, Reward, Rewarding
from pet2win.core.consent import Consent
from pet2win.core.onboarding import OnboardingState, Step
from pet2win.core.posture_input import Issue
from pet2win.core.profile import Profile
from pet2win.core.setup_check import DirectionCheck, Mirror, SetupInfo

from . import form_kit as F
from . import platform_win

US_STATES = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
             "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR",
             "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"]


class OnboardingWindow(QWidget):
    def __init__(self, controller):
        super().__init__(None, Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowMinimizeButtonHint | Qt.WindowTitleHint)
        self._controller = controller
        self.setWindowTitle("赛博背背佳 · 第一次设置")
        self.setAttribute(Qt.WA_DeleteOnClose, False)

    def closeEvent(self, event):
        self._controller.window_will_close()
        super().closeEvent(event)


class OnboardingController:
    """hooks：log(kind, details)、set_pet_scene(scene 或 None)、pet_frame() → 桌宠窗口的位置（QRect）或 None、
    reward_seconds() → 秒、closed(done)。"""

    def __init__(self, data, posture_app, hooks):
        self.data = data
        self.posture_app = posture_app
        self.hooks = hooks
        self.state = data.onboarding
        self.window = OnboardingWindow(self)
        self.window.setFixedWidth(540)
        self.window.setMinimumHeight(600)
        self.header = F.text("", size=12, color=F.SECONDARY)
        self.title_label = F.text("", size=20, weight=QFont.DemiBold)
        self.pet_line = F.text("", size=13.5, weight=QFont.Medium, color=F.TEAL)
        self.body_widget = QWidget()
        self.body = QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(12)
        self.body.setAlignment(Qt.AlignTop)
        self.back_button = F.push("上一步", self._go_back)
        self.skip_button = F.push("跳过", self._skip)
        self.next_button = F.push("下一步", self._go_next, primary=True)

        top = QVBoxLayout()
        top.setSpacing(10)
        for w in (self.header, self.title_label, self.pet_line, self.body_widget):
            top.addWidget(w)
        top.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.back_button)
        bottom.addStretch(1)
        bottom.addWidget(self.skip_button)
        bottom.addWidget(self.next_button)
        outer = QVBoxLayout(self.window)
        outer.setContentsMargins(28, 22, 22, 18)
        outer.addLayout(top, 1)
        outer.addLayout(bottom)

        self.timers = []
        self.consent_form = None
        self.profile_save = None
        self.setup_draft = SetupInfo()
        self.check_passed = False
        self.calibrated_this_step = False
        self.direction_tries = 0
        self.step_ready = True
        self.calibration_status = None
        self.selftest_status = None
        posture_app.on_once_finished = self._once_finished

    @property
    def is_active(self):
        return self.window.isVisible()

    @staticmethod
    def now():
        return time.time()

    def restart(self):
        """从头再走一遍（菜单“重新走一遍引导”）。"""
        self.state = OnboardingState()
        self._save()

    def show(self):
        self._render()
        self.window.adjustSize()
        pet = self.hooks["pet_frame"]()
        screen = None
        if pet is not None:
            screen = QGuiApplication.screenAt(pet.center())
        screen = screen or QGuiApplication.primaryScreen()
        vis = screen.availableGeometry()
        w, h = self.window.width(), self.window.height()
        if pet is not None:
            x = pet.left() - w - 8
            if x < vis.left():
                x = min(pet.right() + 8, vis.right() - w)
            y = max(vis.top(), min(pet.bottom() - h, vis.bottom() - h))
        else:
            x, y = vis.center().x() - w // 2, vis.center().y() - h // 2
        self.window.move(x, y)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.hooks["log"]("onboarding_shown", {"step": str(int(self.state.step))})

    def close(self):
        if self.is_active:
            self.window.close()

    def window_will_close(self):
        self._stop_timers()
        self.hooks["set_pet_scene"](None)
        if not self.state.done:
            self.hooks["log"]("onboarding_paused", {"step": str(int(self.state.step))})
        self.hooks["closed"](self.state.done)

    # ---------- 翻页 ----------

    def _save(self):
        self.state.updated_at = self.now()
        self.data.save_onboarding(self.state)

    def _go(self, step):
        self._stop_timers()
        self.hooks["set_pet_scene"](None)
        self.state.step = step
        self._save()
        self.hooks["log"]("onboarding_step", {"step": str(int(step)), "name": step.title})
        self._render()

    def _go_back(self):
        if self.state.step.previous is not None:
            self._go(self.state.step.previous)

    def _skip(self):
        if self.state.step == Step.SELFTEST:
            self.state.selftest_deferred_at = self.now()
            self.state.selftest_reminded = False
            self.hooks["log"]("onboarding_selftest_deferred", {})
        else:
            self.hooks["log"]("onboarding_skipped", {"step": str(int(self.state.step))})
        if self.state.step.next is not None:
            self._go(self.state.step.next)

    def _go_next(self):
        step = self.state.step
        if step == Step.CONSENT:
            choice = self.consent_form.choice(self.now()) if self.consent_form else None
            if choice is None:
                return
            record = self.data.consent
            profile = self.data.profile
            Consent.record(choice, profile.country if profile else None, record)
            self.data.save_consent(record)
            self.hooks["log"]("consent_saved", self._consent_details(record.current))
        elif step == Step.PROFILE:
            if not (self.profile_save and self.profile_save()):
                return
        elif step == Step.PLACEMENT:
            if self.setup_draft.checked_at is None:
                self.setup_draft.checked_at = self.now()
            self.data.save_setup(self.setup_draft)
        elif step == Step.FINISH:
            self.state.done = True
            self._save()
            self.hooks["log"]("onboarding_done", {})
            self.window.close()
            return
        if step.next is not None:
            self._go(step.next)

    @staticmethod
    def _consent_details(c):
        if c is None:
            return {}
        b = lambda v: "1" if v else "0"   # noqa: E731
        return {"version": c.version, "adult": b(c.adult), "c1": b(c.c1), "c2": b(c.c2), "c3": b(c.c3), "c4": b(c.c4),
                "c5": b(c.c5), "c6": b(c.c6)}

    def _stop_timers(self):
        for t in self.timers:
            t.stop()
            t.deleteLater()
        self.timers = []

    def _every(self, seconds, block):
        t = QTimer(self.window)
        t.setInterval(int(seconds * 1000))
        t.timeout.connect(block)
        t.start()
        self.timers.append(t)

    def _set_body(self, views):
        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for v in views:
            self.body.addWidget(v)

    def _update_buttons(self):
        step = self.state.step
        self.back_button.setVisible(step.previous is not None)
        self.skip_button.setVisible(step.skippable)
        self.skip_button.setText("以后再做" if step == Step.SELFTEST else "跳过")
        self.next_button.setText("完成" if step == Step.FINISH else ("开始" if step == Step.WELCOME else "下一步"))
        self.next_button.setEnabled(self.step_ready)

    def _ready(self, on):
        self.step_ready = on
        self._update_buttons()

    def _render(self):
        step = self.state.step
        self.header.setText(f"第 {int(step)} / {len(list(Step))} 步")
        self.title_label.setText(step.title)
        self.pet_line.setText("")
        self.step_ready = True
        self.consent_form = None
        self.profile_save = None
        {
            Step.WELCOME: self._render_welcome, Step.CONSENT: self._render_consent, Step.PROFILE: self._render_profile,
            Step.PLACEMENT: self._render_placement, Step.CALIBRATE: self._render_calibrate,
            Step.DIRECTION: self._render_direction, Step.MEET_PET: self._render_meet_pet,
            Step.SELFTEST: self._render_selftest, Step.FINISH: self._render_finish,
        }[step]()
        self._update_buttons()

    # ---------- 1 欢迎 ----------

    def _render_welcome(self):
        self.pet_line.setText("桌宠：你好，我是赛博背背佳！")
        self._set_body([
            F.text("我会通过摄像头看你的头和肩膀，坐歪太久就提醒你，改过来了给你比个心。"),
            F.text("大约 8 分钟，随时可以停，下次打开会接着来。", weight=QFont.DemiBold),
        ])

    # ---------- 2 同意 ----------

    def _render_consent(self):
        form = F.ConsentForm(self.data.consent.current)
        form.on_change = lambda: self._ready(form.is_complete)
        self.consent_form = form
        self._set_body([form.view])
        self.step_ready = form.is_complete

    # ---------- 3 基本资料 ----------

    def _render_profile(self):
        existing = self.data.profile
        p = existing or Profile()
        age = F.Number(float(p.age) if p.age is not None else None, "岁")
        height = F.Number(p.height_cm, "cm")
        weight = F.Number(p.weight_kg, "kg")
        if existing is not None:
            if p.age is None:
                age.set_declined(True)
            if p.height_cm is None:
                height.set_declined(True)
            if p.weight_kg is None:
                weight.set_declined(True)
        sex = F.Choice([("female", "女"), ("male", "男"), ("other", "其他"), ("na", "不想说")], p.sex)
        hand = F.Choice([("right", "右手"), ("left", "左手"), ("both", "两只手都行"), ("na", "不想说")], p.handedness)
        sit = F.Choice([("<4", "不到 4 小时"), ("4-6", "4–6 小时"), ("6-8", "6–8 小时"), ("8-10", "8–10 小时"),
                        (">10", "10 小时以上"), ("na", "不想说")], p.sit_hours)
        job = F.Choice([("student", "学生"), ("it", "程序 / IT"), ("design", "设计"), ("office", "办公室文职"),
                        ("health", "医疗 / 康复"), ("other", "其他"), ("na", "不想说")], p.occupation)
        neck = F.Choice([("no", "没有"), ("yes", "有"), ("na", "不想说")], p.neck_history)
        country = F.Choice([("US", "美国"), ("CN", "中国"), ("other", "其他"), ("na", "不想说")], p.country)
        state = F.Choice([("na", "不想说")] + [(s, s) for s in US_STATES], p.us_state)
        state_row = F.labeled("所在州", state.popup)
        country.on_change = lambda: state_row.setVisible(country.value == "US")
        state_row.setVisible(country.value == "US")

        health = self.data.consent.current is not None and self.data.consent.current.c3 is True
        warning = F.note("")
        F.set_color(warning, F.RED)
        self._set_body([
            F.note("每一题都可以选“不想说”。" + ("你勾了 ③：导出时身高取整到 5 cm、体重取整到 5 kg。" if health
                                           else "你没勾 ③：身高、体重和颈肩病史只存在这台电脑上，导出时不带。")),
            F.labeled("年龄", age.view),
            F.labeled("性别", sex.popup),
            F.labeled("身高", height.view),
            F.labeled("体重", weight.view),
            F.labeled("惯用手", hand.popup),
            F.labeled("每天坐多久", sit.popup),
            F.labeled("职业", job.popup),
            F.labeled("颈肩病史", neck.popup),
            F.labeled("国家 / 地区", country.popup),
            state_row,
            warning,
        ])

        def save():
            age_value = int(age.value) if age.value is not None else None
            if not age.decline.isChecked() and not age.is_empty and (age_value is None or age_value < 1 or age_value > 120):
                warning.setText("年龄请填数字，或者勾“不想说”。")
                return False
            consent = self.data.consent.current
            if age_value is not None and age_value < 18 and consent is not None and consent.adult:
                warning.setText("你填的年龄不满 18 岁：请回上一步把“满 18 岁了”改成“还没有”。")
                return False
            for n in (height, weight):
                if not n.decline.isChecked() and not n.is_empty and n.value is None:
                    warning.setText("身高、体重请填数字，或者勾“不想说”。")
                    return False
            out = Profile()
            out.age = age_value
            out.sex = sex.value
            out.height_cm = height.value
            out.weight_kg = weight.value
            out.handedness = hand.value
            out.sit_hours = sit.value
            out.occupation = job.value
            out.neck_history = neck.value
            out.country = country.value
            out.us_state = state.value if out.country == "US" else "na"
            self.data.save_profile(out)
            self.hooks["log"]("profile_saved", {"age_band": Profile.age_band(out.age), "country": out.country})
            return True

        self.profile_save = save

    # ---------- 4 摆放 ----------

    def _render_placement(self):
        self.setup_draft = self.data.setup or SetupInfo()
        camera = F.Choice([("builtin", "电脑自带的摄像头"), ("external", "外接摄像头")], self.setup_draft.camera)
        place = F.Choice([("desk", "直接放在桌上"), ("stand", "放在支架上")], self.setup_draft.placement)
        camera.on_change = lambda: setattr(self.setup_draft, "camera", camera.value)
        place.on_change = lambda: setattr(self.setup_draft, "placement", place.value)
        status = F.text("正在检查体态测试…", color=F.SECONDARY)
        retry = F.push("重新检查")
        retry.setVisible(False)
        self.pet_line.setText("桌宠：头和两个肩膀都要在画面里哦。")
        self._set_body([
            F.labeled("用哪个摄像头", camera.popup),
            F.labeled("电脑放在", place.popup),
            F.text("坐在平时的位置，让头和两个肩膀都在画面里。电脑离你远一点比近一点好。"),
            status, F.row([retry, None]),
        ])
        self.step_ready = self.check_passed

        def run():
            status.setText("正在检查体态测试…")
            F.set_color(status, F.SECONDARY)
            retry.setVisible(False)

            def done(ok, message):
                self.check_passed = ok
                status.setText(message)
                F.set_color(status, F.GREEN if ok else F.RED)
                retry.setVisible(not ok)
                if self.state.step == Step.PLACEMENT:
                    self._ready(ok)
            self._run_check(done)

        retry.clicked.connect(run)
        run()

    def _run_check(self, done):
        """--check：不开摄像头，只看模型和字体能不能用。"""
        if self.posture_app.executable is None:
            done(False, "没找到“体态测试”。它应该在桌宠所在文件夹里的“体态测试”文件夹里：请把下载的压缩包完整解压，再点“重新检查”。")
            return

        def finished(code, line):
            if code == 0:
                done(True, "体态测试装好了，能用。")
            else:
                done(False, f"体态测试自检没通过（退出码 {code}）：{line}\n请重新解压完整的压缩包，再点“重新检查”。")
        self.posture_app.check(finished)

    # ---------- 5 校准 ----------

    def _render_calibrate(self):
        status = F.text("", color=F.SECONDARY)
        start = F.push("开始校准")
        settings = F.push("打开相机权限设置", platform_win.open_camera_settings)
        settings.setVisible(False)
        self.pet_line.setText("桌宠：先坐成标准的正坐，我拿它当尺子，之后看你有没有弯腰、歪头。")
        views = [
            F.text("双脚平放踩地，坐稳，背自然立起来，头摆正，肩放松，看屏幕中间。不用绷得特别直，太用力反而会累。"),
            F.text("点“开始校准”，体态测试会开一个窗口。保持这个姿势别动，等它说好，做完窗口会自己关。"),
            F.row([start, None]), status, F.row([settings, None]),
        ]
        t = self.data.baseline_time
        if t is not None and not self.calibrated_this_step:
            lt = time.localtime(t)
            when = f"{lt.tm_mon} 月 {lt.tm_mday} 日 {lt.tm_hour:02d}:{lt.tm_min:02d}"
            views.insert(2, F.note(f"以前校准过（{when}），可以直接下一步，也可以重新校准。"))
        self._set_body(views)
        self.step_ready = self.calibrated_this_step or self.data.baseline_time is not None
        before = self.data.baseline_time

        def calibration_status(code):
            start.setEnabled(True)
            now_t = self.data.baseline_time
            if now_t is not None and now_t != before and (code == 0 or now_t > (before or 0)):
                self.data.archive_baseline()
                self.calibrated_this_step = True
                status.setText("校准好了。")
                F.set_color(status, F.GREEN)
                self.hooks["log"]("onboarding_calibrated", {"time": str(int(now_t))})
                self._ready(True)
            elif code == 4:
                status.setText("摄像头打不开：去 设置 → 隐私和安全性 → 相机，打开“允许桌面应用访问相机”，"
                               "再关掉正在用摄像头的软件（视频会议之类），然后点“开始校准”。")
                F.set_color(status, F.RED)
                settings.setVisible(True)
            elif code == 3:
                status.setText("体态测试已经开着一份：先把它的窗口关掉，再点“开始校准”。")
                F.set_color(status, F.RED)
            else:
                status.setText(f"这次没校准成功（退出码 {code}），再试一次。")
                F.set_color(status, F.RED)

        self.calibration_status = calibration_status

        def on_start():
            if self.posture_app.executable is None:
                status.setText("没找到体态测试，回上一步检查。")
                return
            start.setEnabled(False)
            status.setText("校准窗口开着…做完它会自己关。")
            F.set_color(status, F.SECONDARY)
            self.posture_app.run_once("calibrate", "校准")

        start.clicked.connect(on_start)

    def _once_finished(self, mode, code):
        if mode == "calibrate" and self.state.step == Step.CALIBRATE and self.calibration_status:
            self.calibration_status(code)
        if mode == "selftest" and self.state.step == Step.SELFTEST and self.selftest_status:
            self.selftest_status(code)

    # ---------- 6 方向检查 ----------

    def _render_direction(self):
        status = F.text("", color=F.SECONDARY)
        start = F.push("开始")
        recalibrate = F.push("重新校准", lambda: self._go(Step.CALIBRATE))
        recalibrate.setVisible(False)
        self.direction_tries = 0
        self._set_body([
            F.text("看看摄像头的左右对不对。点“开始”，然后把头往你自己的左边歪，保持 3 秒。"),
            F.row([start, None]), status, F.row([recalibrate, None]),
        ])
        self.setup_draft = self.data.setup or SetupInfo()
        self.step_ready = self.setup_draft.checked_at is not None and self.setup_draft.mirrored is not None
        self.posture_app.start_background()

        def on_start():
            start.setEnabled(False)
            recalibrate.setVisible(False)

            def then():
                self.pet_line.setText("桌宠：头往你自己的左边歪，保持 3 秒～")
                left = [4]
                status.setText(f"保持… {left[0]}")
                began = self.now()

                def tick():
                    left[0] -= 1
                    if left[0] > 0:
                        status.setText(f"保持… {left[0]}")
                        return
                    self._stop_timers()
                    self._finish_direction(began + 1, status, start, recalibrate)
                self._every(1, tick)

            self._wait_for_rows(status, then)

        start.clicked.connect(on_start)

    def _wait_for_rows(self, status, then):
        """等后台的体态测试开始写行（刚启动要加载模型）。"""
        since = self.now() - 3
        status.setText("等体态测试看到你…")
        F.set_color(status, F.SECONDARY)
        waited = [0.0]

        def tick():
            waited[0] += 0.5
            text = DirectionCheck.read_tail(self.data.log_path, 64 * 1024) or ""
            if DirectionCheck.rows(text, max(since, self.now() - 2)):
                self._stop_timers()
                then()
                return
            if waited[0] >= 30:
                self._stop_timers()
                status.setText("30 秒还没看到你：确认已经校准过、摄像头对着你，再点“开始”。"
                               f"（{self.posture_app.status_text or '后台在跑'}）")
                F.set_color(status, F.RED)
                self._ready(True)   # 可以先这样
        self._every(0.5, tick)

    def _finish_direction(self, since, status, start, recalibrate):
        text = DirectionCheck.read_tail(self.data.log_path, 64 * 1024) or ""
        r = DirectionCheck.evaluate(text, since)
        self.direction_tries += 1
        start.setEnabled(True)
        self.pet_line.setText("")
        self.hooks["log"]("direction_check", {"try": str(self.direction_tries), "rows": str(r.rows), "mirror": r.mirror.value,
                                              "shoulders": "" if r.shoulders_visible is None else ("1" if r.shoulders_visible else "0")})
        if r.mirror == Mirror.UNCLEAR and self.direction_tries < 2:
            status.setText("没看清，再来一次：头往你自己的左边多歪一点。")
            F.set_color(status, F.ORANGE)
            return
        self.setup_draft.mirrored = {Mirror.NOT_MIRRORED: False, Mirror.MIRRORED: True}.get(r.mirror)
        self.setup_draft.shoulders_visible = r.shoulders_visible
        self.setup_draft.checked_at = self.now()
        self.data.save_setup(self.setup_draft)
        lines = [{Mirror.NOT_MIRRORED: "方向是对的。", Mirror.MIRRORED: "你的摄像头是镜像的，我记下了，以后说左右时会对调。",
                  Mirror.UNCLEAR: "还是没看清左右，先这样，不影响使用。"}[r.mirror]]
        if r.shoulders_visible is False:
            lines.append("两个肩膀没有完全在画面里：把电脑往后挪一点，然后重新校准。也可以先这样。")
            recalibrate.setVisible(True)
        status.setText("\n".join(lines))
        F.set_color(status, F.ORANGE if r.shoulders_visible is False else F.GREEN)
        start.setText("再测一次")
        self.direction_tries = 0
        self._ready(True)

    # ---------- 7 认识桌宠（只演示：不写提醒记录、不占提醒限额） ----------

    def _render_meet_pet(self):
        status = F.text("", color=F.SECONDARY)
        start = F.push("开始")
        self._set_body([
            F.text("来认识一下我会怎么提醒你。点“开始”，然后低头看手机 10 秒，看看桌宠的样子；再抬头坐正。"),
            F.note("这只是演示，不会算进提醒次数。"),
            F.row([start, None]), status,
        ])
        self.posture_app.start_background()

        def on_start():
            start.setEnabled(False)
            self._wait_for_rows(status, lambda: self._run_meet_pet(status, start))

        start.clicked.connect(on_start)

    def _run_meet_pet(self, status, start):
        self.pet_line.setText("桌宠：低头看看手机，保持 10 秒～")
        status.setText("")
        down_seconds = [0.0]
        last_seen = [self.now()]
        phase = [0]   # 0 等低头够 10 秒，1 等抬头，2 奖励中

        def tick():
            text = DirectionCheck.read_tail(self.data.log_path, 64 * 1024) or ""
            rows = [r for r in DirectionCheck.rows(text, self.now() - 2.5) if r.judged]
            if rows:
                last_seen[0] = self.now()
            down = bool(rows) and sum(1 for r in rows if Issue.HEAD_DOWN in r.issues) >= len(rows) * 0.5
            if phase[0] == 0:
                if down:
                    down_seconds[0] += 0.5
                    self.hooks["set_pet_scene"](Drifting(Issue.HEAD_DOWN, None, min(1.0, down_seconds[0] / 10)))
                    status.setText(f"低头 {int(down_seconds[0])} / 10 秒")
                else:
                    self.hooks["set_pet_scene"](None)
                if down_seconds[0] >= 10:
                    phase[0] = 1
                    self.pet_line.setText("桌宠：好啦，现在抬头坐正～")
            elif phase[0] == 1:
                if down:
                    self.hooks["set_pet_scene"](Drifting(Issue.HEAD_DOWN, None, 1.0))
                elif rows:
                    phase[0] = 2
                    self.hooks["set_pet_scene"](Rewarding(Reward(id=-1, issue=Issue.HEAD_DOWN, line="抬头了！就是这样 ♥",
                                                                 started_at=self.now())))
                    self.pet_line.setText("桌宠：你坐歪太久我会提醒你，改过来我就给你比个心。")
                    status.setText("")

                    def after():
                        if self.state.step != Step.MEET_PET:
                            return
                        self.hooks["set_pet_scene"](None)
                        start.setEnabled(True)
                        self._ready(True)
                    QTimer.singleShot(int(self.hooks["reward_seconds"]() * 1000), after)
            if self.now() - last_seen[0] > 10:
                status.setText("没看到你：体态测试在后台开着吗？也可以先跳过这一步。")
                F.set_color(status, F.ORANGE)
            else:
                F.set_color(status, F.SECONDARY)

        self._every(0.5, tick)

    # ---------- 8 摸底测试 ----------

    def _render_selftest(self):
        status = F.text("", color=F.SECONDARY)
        start = F.push("开始摸底测试")
        self.pet_line.setText("桌宠：看看你的脖子往各个方向能动多少～")
        self._set_body([
            F.text("体态测试会开一个窗口，跟着提示慢慢转头、歪头，大约 3 分钟。做完窗口会自己关。"),
            F.note("现在没空可以点“以后再做”，明天这个时候我会问你一次。"),
            F.row([start, None]), status,
        ])
        self.step_ready = False

        def selftest_status(code):
            start.setEnabled(True)
            if code == 0:
                status.setText("做完了。")
                F.set_color(status, F.GREEN)
                self.state.selftest_deferred_at = None
                self._save()
                self._ready(True)
            elif code == 4:
                status.setText("摄像头打不开：去 设置 → 隐私和安全性 → 相机，打开“允许桌面应用访问相机”。")
                F.set_color(status, F.RED)
            else:
                status.setText(f"没做完（退出码 {code}）。可以再试，或者“以后再做”。")
                F.set_color(status, F.ORANGE)

        self.selftest_status = selftest_status

        def on_start():
            start.setEnabled(False)
            status.setText("摸底测试窗口开着…")
            self.posture_app.run_once("selftest", "摸底测试")   # 先停后台，做完再开回来

        start.clicked.connect(on_start)

    # ---------- 9 完成 ----------

    def _render_finish(self):
        self.pet_line.setText("桌宠：设置好啦！")
        self._set_body([
            F.text("我会在后台看着。坐歪太久我会提醒你，改过来给你比个心；不想被打扰就点我选“稍后”。"),
            F.note("资料、勾选和导出都在菜单“我的数据…”里（右键点桌宠，或者右下角托盘里的小图标）。"),
        ])
        self.posture_app.start_background()
