"""照翻 Pet2/Mac/main.swift + AppDelegate.swift：宿主。读体态软件的两个文件 → Brain → 每帧画到桌宠窗口。
只有一个桌宠窗口；托盘图标的菜单和右键菜单是同一份菜单（Mac 是菜单栏图标）。

启动：python -m pet2win（开发）或 赛博背背佳.exe（打包后）。--check：不开窗口，把每一幕画一遍、检查素材，打一行 OK 就退出（打包后自检用）。
"""
import concurrent.futures
import os
import sys
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QPoint, QRect, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCursor, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon, QWidget

from pet2win.core.brain import (Away, Brain, Calm, Drifting, Paused, ReminderKind, Reminding, Reward, Rewarding, Settings,
                                Snoozed, Waiting)
from pet2win.core.csv_tail import CSVTail, MISSING
from pet2win.core.event_log import EventLog
from pet2win.core.posture_input import AlertRow, Issue, PostureRow
from pet2win.core.rewards import RewardKind, RewardRules

from . import platform_win, resources
from . import pet_canvas as PC
from .my_data import MyData
from .posture_app import PostureApp


def alerts_path(log_path):
    """后端的命名规则：log.csv → log-alerts.csv（放在一起）。"""
    folder, name = os.path.split(log_path)
    stem, ext = os.path.splitext(name)
    return os.path.join(folder, f"{stem}-alerts{ext}")


class PetWindow(QWidget):
    """桌宠窗口：无边框、透明、总在最前、不抢焦点、不在任务栏。"""

    def __init__(self, state):
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus \
            | Qt.NoDropShadowWindowHint
        super().__init__(None, flags)
        self.st = state
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(int(PC.WIDTH), int(PC.HEIGHT))
        self.setWindowTitle("赛博背背佳")
        self.setAccessibleName("赛博背背佳桌宠；拖动可以移动，右键打开菜单")
        self.on_bubble_click = None
        self.on_right_click = None
        self.on_moved = None
        self._drag_start = None
        self._window_start = None
        self._moved = False
        self._layer = None
        self._click_through = None

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        self._layer = PC.paint(p, self.st, dpr=self.devicePixelRatioF(), layer_cache=self._layer)
        p.end()

    def mac_point(self, pos):
        return (pos.x(), PC.HEIGHT - pos.y())

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            if self.on_right_click:
                self.on_right_click(e.globalPosition().toPoint())
            return
        if e.button() == Qt.LeftButton:
            self._drag_start = e.globalPosition().toPoint()
            self._window_start = self.pos()
            self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag_start is None:
            return
        d = e.globalPosition().toPoint() - self._drag_start
        if abs(d.x()) + abs(d.y()) > 3:
            self._moved = True
        self.move(self._window_start + d)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._drag_start is None:
            return
        self._drag_start = None
        if self._moved:
            if self.on_moved:
                self.on_moved()
            return
        x, y = self.mac_point(e.position())
        if self.st.bubble_text is not None and (self.st.bubble_rect.contains(x, y) or self.st.interactive_rect().contains(x, y)):
            if self.on_bubble_click:
                self.on_bubble_click(e.globalPosition().toPoint())

    def update_click_through(self):
        """透明的地方让鼠标穿过去（按着鼠标键的时候不改，免得拖到一半掉了）。"""
        if not platform_win.IS_WINDOWS or platform_win.mouse_buttons_down():
            return
        pos = self.mapFromGlobal(QCursor.pos())
        x, y = pos.x(), PC.HEIGHT - pos.y()
        inside = self.st.interactive_rect().contains(x, y)
        want = not inside
        if want != self._click_through:
            self._click_through = platform_win.set_click_through(int(self.winId()), want)


class SleepFilter(QAbstractNativeEventFilter):
    """电脑要睡了：记一笔（Mac 是 NSWorkspace.willSleepNotification）。"""

    def __init__(self, on_sleep):
        super().__init__()
        self.on_sleep = on_sleep

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) == b"windows_generic_MSG":
                import ctypes
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0218 and msg.wParam == 0x4:   # WM_POWERBROADCAST, PBT_APMSUSPEND
                    self.on_sleep()
        except Exception:   # noqa: BLE001 —— 记不上就算了，不能让系统消息处理出错
            pass
        return False, 0


class Host(QObject):
    _polled = Signal(object, object, object, object, object)   # (log 的 tail, alerts 的 tail, 行, 结果行, 状态)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = QSettings("guigui", "BeibeijiaPet2Win")
        self.canvas = PC.CanvasState(resources.assets_dir())
        try:
            self.log = EventLog()
        except OSError:
            self.log = None
        self.posture_app = PostureApp(self)
        self.window = PetWindow(self.canvas)
        self.my_data = MyData(self.posture_app, self.canvas, lambda: self.window, self._log)
        self.brain = None
        self.log_tail = None
        self.alert_tail = None
        self.hidden = False
        self.reward_preview = None    # (scene, until)
        self.preview_id = -100
        self.file_status = MISSING
        self._polling = False
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="poll")
        self._polled.connect(self._on_polled)
        self._last_frame = time.monotonic()
        self._tray_tip = None

        self._make_brain()
        self._open_files()
        self.posture_app.on_event = self._log
        self.posture_app.on_change = lambda _s: self._update_input_status()
        if self.manages_posture_app:
            self.posture_app.start_background()

        self.canvas.gif_px_scale = max(1.0, self.window.devicePixelRatioF())
        self._place_window()
        self.window.show()
        self.window.on_bubble_click = self._show_bubble_menu
        self.window.on_right_click = self._show_context_menu
        self.window.on_moved = self._save_window_position
        self.canvas.on_reward_presented = self._reward_presented

        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._rebuild_menu)
        self.menu.aboutToHide.connect(self._menu_closed)
        self.tray = QSystemTrayIcon(self._tray_icon(), self)
        self.tray.setToolTip("赛博背背佳")
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        self._rebuild_menu()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_files)
        self._poll_timer.start(500)
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(1000)
        self._frame_timer = QTimer(self)
        self._frame_timer.setTimerType(Qt.PreciseTimer)
        self._frame_timer.timeout.connect(self._frame)
        self._frame_timer.start(16)
        self._poll_files()

        self._sleep_filter = SleepFilter(lambda: self._log("system_sleep", {}))
        app.installNativeEventFilter(self._sleep_filter)
        app.aboutToQuit.connect(self._will_quit)
        self._log("app_started", {"demo": "1" if self.demo else "0", "log": self.log_path, "platform": sys.platform})
        self.my_data.start_if_needed()   # 第一次设置没走完：先走引导

    # ---------- 设置 ----------

    @property
    def demo(self):
        return self.settings.value("demo", False, type=bool)

    @property
    def custom_log_path(self):
        v = self.settings.value("logPath", None)
        return v if isinstance(v, str) and v else None

    @property
    def log_path(self):
        """装了“体态测试”：桌宠在后台带着它跑，读它固定的数据文件夹。用户在菜单里选过别的 log.csv，就一直用那个。"""
        return self.custom_log_path or self.posture_app.log_path

    @property
    def manages_posture_app(self):
        return self.custom_log_path is None and self.posture_app.executable is not None

    def _log(self, kind, details):
        if self.log is not None:
            self.log.append(kind, details, time.time())

    def _will_quit(self):
        self.posture_app.stop_synchronously()
        self._log("app_quit", {})
        self.tray.hide()

    def _make_brain(self):
        settings = Settings.demo() if self.demo else Settings()
        # 奖励：放哪个 GIF 见 core/rewards.py；每种整段演多久按 GIF 实际长度算。
        settings.rewards.seconds = self.canvas.reward_lengths()
        settings.reward_seconds = self.canvas.reward_seconds(RewardKind.HEART)
        paused = self.brain.paused if self.brain is not None else False
        self.brain = Brain(settings)
        self.brain.on_event = lambda kind, details, t: self.log is not None and self.log.append(kind, details, t)
        self.brain.input_status = "正在打开 log.csv…"
        if paused:
            self.brain.set_paused(True, time.time())

    def _open_files(self):
        self.log_tail = CSVTail(self.log_path)
        self.alert_tail = CSVTail(alerts_path(self.log_path))

    # ---------- 读文件（放在后台线程：第一次读一个很大的 log.csv 要一会儿，不能让桌宠跟着卡） ----------

    def _poll_files(self):
        if self.brain.paused or self._polling:
            return
        self._polling = True
        log_tail, alert_tail = self.log_tail, self.alert_tail

        def work():
            try:
                rows = [r for r in (PostureRow.from_csv(d) for d in log_tail.poll()) if r is not None]
                alerts = [a for a in (AlertRow.from_csv(d) for d in alert_tail.poll()) if a is not None]
                status = log_tail.status
            except Exception:   # noqa: BLE001
                rows, alerts, status = [], [], log_tail.status
            self._polled.emit(log_tail, alert_tail, rows, alerts, status)

        self._pool.submit(work)

    def _on_polled(self, log_tail, _alert_tail, rows, alerts, status):
        self._polling = False
        # 期间换了文件（选了新的 log.csv、切了演示）：这批作废。
        if log_tail is not self.log_tail or self.brain.paused:
            return
        if not self.my_data.onboarding_active:   # 引导开着时不提醒、不占限额
            now = time.time()
            for row in self.my_data.adjust(rows):   # 镜像时左右对调
                self.brain.ingest(row, now)
            for alert in alerts:
                self.brain.ingest_alert(alert, now)
        self.file_status = status
        self._update_input_status()

    def _update_input_status(self):
        """没数据时脚下那行字：体态测试有话要说（没校准、摄像头、出错）就先说它的，否则说读文件的情况。"""
        if self.manages_posture_app and self.posture_app.status_text:
            self.brain.input_status = self.posture_app.status_text
            return
        self.brain.input_status = {
            "missing": "找不到 log.csv：体态检查开了吗？",
            "unreadable": "读不了 log.csv：文件被占用或没有权限",
            "waiting_header": "log.csv 还没有表头，等体态检查写入",
            "reading": "体态检查连上了，等它看到人（要先校准）",
        }[self.file_status.kind]

    # ---------- 计时 ----------

    def _tick(self):
        now = time.time()
        self.brain.tick(now)
        self.my_data.tick(self.brain.scene(now))

    def _frame(self):
        uptime = time.monotonic()
        dt = uptime - self._last_frame
        self._last_frame = uptime
        now = time.time()
        if self.reward_preview is not None and now >= self.reward_preview[1]:
            self.reward_preview = None
        scene = (self.reward_preview[0] if self.reward_preview else None) or self.my_data.scene_override or self.brain.scene(now)
        # 犯困 / 睡着按“这个姿势离提醒门槛多远”来演（只读 Brain 算好的数）。
        if isinstance(scene, Drifting) and self.brain.latest_row is not None:
            self.canvas.drowsiness = self.brain.reminder_progress(scene.issue, self.brain.latest_row.t)
        else:
            self.canvas.drowsiness = None
        self.canvas.noticing = self.brain.reminder_sees_correction
        self.canvas.step(scene, uptime, dt)
        if not self.hidden:
            self.window.update()
            self.window.update_click_through()
        # 省电：提醒、奖励、刚换幕、气泡在弹的时候 60 帧，平常 30 帧（弹簧按真实间隔算，慢一点也不走样）。
        busy = isinstance(scene, (Reminding, Rewarding)) or uptime - self.canvas.act_changed_at < 2.5 \
            or abs(self.canvas.bubble_scale.v) > 0.05 or self.window._drag_start is not None
        interval = 16 if busy else 33
        if self._frame_timer.interval() != interval:
            self._frame_timer.setInterval(interval)
        tip = "赛博背背佳 · " + self._scene_text(scene)
        if tip != self._tray_tip:
            self._tray_tip = tip
            self.tray.setToolTip(tip)

    def _reward_presented(self, reward_id, played):
        if reward_id < 0:
            return   # 负数 id 是引导里的演示、菜单里的“看看奖励”，不记
        self.brain.reward_presented(reward_id, played, time.time())

    # ---------- 菜单 ----------

    def _menu_closed(self):
        """点了菜单（不管哪一项）：后台没起来的话，退避从头来、马上再试。"""
        def retry():
            if self.manages_posture_app and not self.brain.paused:
                self.posture_app.retry_now()
        QTimer.singleShot(0, retry)

    def _scene_text(self, scene=None):
        s = scene if scene is not None else self.brain.scene(time.time())
        if isinstance(s, Waiting):
            return s.text
        if isinstance(s, Away):
            return "没看到人"
        if isinstance(s, Calm):
            return "坐得挺好"
        if isinstance(s, Drifting):
            return f"看到：{s.issue.cn}"
        if isinstance(s, Reminding):
            r = s.reminder
            return "提醒中：" + (r.issue.cn if r.issue else ("坐着不动太久" if r.kind == ReminderKind.UPRIGHT else "久坐"))
        if isinstance(s, Rewarding):
            return "刚改过来，奖励中"
        if isinstance(s, Snoozed):
            return "安静中"
        return "已暂停"

    def _rebuild_menu(self):
        m = self.menu
        m.clear()
        title = m.addAction("赛博背背佳 · Windows 版")
        title.setEnabled(False)
        status = m.addAction(self._scene_text())
        status.setEnabled(False)
        m.addSeparator()
        if isinstance(self.brain.scene(time.time()), Snoozed):
            m.addAction("结束安静", lambda: self.brain.cancel_snooze(time.time()))
        else:
            a = m.addAction("稍后（安静 10 分钟）", lambda: self.brain.snooze(time.time()))
            a.setEnabled(not self.brain.paused)
        m.addAction("恢复" if self.brain.paused else "暂停", self._toggle_pause)
        m.addAction("显示桌宠" if self.hidden else "隐藏桌宠", self._toggle_hidden)
        rewards = m.addMenu("看看奖励")
        for kind, label in [(RewardKind.HEART, "送爱心（第 1 次、第 5 次…）"), (RewardKind.THUMBS_UP, "点赞（改得快）"),
                            (RewardKind.PLUS1, "+1（其余每次）"), (RewardKind.CHIN_REST, "托腮（久坐回来）")]:
            rewards.addAction(label, lambda k=kind: self._preview_reward(k))
        m.addSeparator()
        installed = self.posture_app.executable is not None
        busy = self.posture_app.state.kind == "foreground"
        if self.posture_app.state.kind == "camera_denied":
            m.addAction("打开相机权限设置…", self.posture_app.open_camera_settings)
        a = m.addAction("校准…", self._calibrate)
        a.setEnabled(installed and not busy)
        a = m.addAction("摸底测试…", lambda: self.posture_app.run_once("selftest", "摸底测试"))
        a.setEnabled(installed and not busy)
        a = m.addAction("正在导出…" if self.my_data.exporting else "导出数据…", self.my_data.export)
        a.setEnabled(installed and not self.my_data.exporting)
        self.my_data.add_menu(m)
        m.addSeparator()
        a = m.addAction("快速演示（20 秒就提醒）", self._toggle_demo)
        a.setCheckable(True)
        a.setChecked(self.demo)
        m.addAction("选择 log.csv…", self._choose_log)
        m.addAction("打开记录文件夹", self._open_records)
        m.addSeparator()
        m.addAction("退出", self.app.quit)

    def _show_context_menu(self, global_pos):
        self._rebuild_menu()
        self.menu.popup(global_pos)

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:   # 左键点托盘图标：也弹菜单
            self._rebuild_menu()
            self.menu.popup(QCursor.pos())

    def _show_bubble_menu(self, global_pos):
        m = self.my_data.bubble_menu()
        if m is None:
            if not isinstance(self.brain.scene(time.time()), Reminding):
                return
            m = QMenu()
            m.addAction("知道了", lambda: self.brain.acknowledge(time.time()))
            m.addAction("稍后（安静 10 分钟）", lambda: self.brain.snooze(time.time()))
        self._bubble_menu = m   # 留个引用，不然菜单会被回收
        m.popup(global_pos)

    def _preview_reward(self, kind):
        self.preview_id -= 1
        issue = None if kind == RewardKind.CHIN_REST else Issue.HEAD_TILT
        today = 2 if kind == RewardKind.PLUS1 else 1
        now = time.time()
        reward = Reward(id=self.preview_id, issue=issue, line=RewardRules.line(kind, issue, today), started_at=now, kind=kind,
                        today=today, seconds=self.canvas.reward_seconds(kind))
        self.reward_preview = (Rewarding(reward), now + reward.seconds)

    def _toggle_pause(self):
        self.brain.set_paused(not self.brain.paused, time.time())
        if self.brain.paused:
            if self.manages_posture_app:
                self.posture_app.stop_background()   # 摄像头灯跟着灭
        else:
            if self.manages_posture_app:
                self.posture_app.start_background()
            self._open_files()
            self._poll_files()

    def _calibrate(self):
        # 先让桌宠提醒一句别绷太紧，停 2 秒再开校准窗口。
        self.canvas.say("舒服地稍微坐直，双脚踩地，别挺得太用力", 3.5)
        QTimer.singleShot(2000, lambda: self.posture_app.run_once("calibrate", "校准"))

    def _toggle_hidden(self):
        self.hidden = not self.hidden
        if self.hidden:
            self.window.hide()
        else:
            self.window.show()
        self._log("pet_hidden" if self.hidden else "pet_shown", {})

    def _toggle_demo(self):
        self.settings.setValue("demo", not self.demo)
        self._make_brain()
        self._open_files()
        if self.manages_posture_app:
            self.posture_app.start_background()
        self._log("demo_mode", {"on": "1" if self.demo else "0"})

    def _choose_log(self):
        start = os.path.dirname(os.path.expanduser(self.log_path))
        path, _ = QFileDialog.getOpenFileName(None, "选择体态检查写的 log.csv", start, "CSV 文件 (*.csv)")
        if not path:
            return
        self.settings.setValue("logPath", os.path.normpath(path))
        self._open_files()
        self._poll_files()

    def _open_records(self):
        if self.log is not None:
            platform_win.open_path(self.log.directory)

    # ---------- 位置 ----------

    def _place_window(self):
        w, h = self.window.width(), self.window.height()
        x = self.settings.value("petX", None)
        y = self.settings.value("petY", None)
        placed = False
        if x is not None and y is not None:
            try:
                rect = QRect(int(float(x)), int(float(y)), w, h)
                if any(s.availableGeometry().intersects(rect) for s in QGuiApplication.screens()):
                    self.window.move(rect.topLeft())
                    placed = True
            except (TypeError, ValueError):
                pass
        if not placed:
            screen = QGuiApplication.primaryScreen()
            vis = screen.availableGeometry()
            self.window.move(vis.right() - w - 16, vis.bottom() - h - 8)

    def _save_window_position(self):
        self.settings.setValue("petX", self.window.x())
        self.settings.setValue("petY", self.window.y())

    def _tray_icon(self):
        img = QImage(os.path.join(resources.assets_dir(), "idle.png"))
        if img.isNull():
            pm = QPixmap(32, 32)
            pm.fill(Qt.darkBlue)
            return QIcon(pm)
        head = img.copy(QRect(250, 110, 760, 760))   # 原画里头的那一块（和 exe 图标一样）
        return QIcon(QPixmap.fromImage(head.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)))


def self_check():
    """打包后自检：素材、GIF、同意书都找得到，每一幕都能画出来。打一行 OK 开头的结果。"""
    from pet2win.core.brain import Lines, Reminder
    t0 = time.time()
    st = PC.CanvasState(resources.assets_dir())
    problems = []
    if not st.rig.loaded:
        problems.append(f"没找到素材 {resources.assets_dir()}")
    if RewardKind.HEART not in st.shows:
        problems.append("没找到奖励 GIF")
    if resources.consent_text_path() is None:
        problems.append("没找到同意书全文")
    reminder = Reminder(id=1, kind=ReminderKind.POSTURE, issue=Issue.HEAD_DOWN, side=None,
                        line=Lines.reminder(Issue.HEAD_DOWN, None, 300), started_at=0)
    scenes = [Calm(), Drifting(Issue.HEAD_DOWN, None, 0.9), Reminding(reminder),
              Rewarding(Reward(id=2, issue=Issue.HEAD_DOWN, line="x", started_at=0)), Snoozed(time.time() + 60), Away(),
              Paused(), Waiting("测试")]
    img = QImage(int(PC.WIDTH * 2), int(PC.HEIGHT * 2), QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(2)
    t = 0.0
    for scene in scenes:
        for _ in range(45):
            st.step(scene, t, 1 / 30)
            t += 1 / 30
        img.fill(Qt.transparent)
        p = QPainter(img)
        PC.paint(p, st, dpr=2)
        p.end()
    # 画出来的角色不能是空的
    painted = sum(1 for x in range(0, img.width(), 7) for y in range(0, img.height(), 7) if img.pixelColor(x, y).alpha() > 0)
    if painted < 50:
        problems.append("角色没画出来")
    backend = resources.backend_executable()
    status = "OK" if not problems else "FAIL " + "；".join(problems)
    print(f"{status} assets={resources.assets_dir()} gifs={','.join(k.value for k in st.shows)} "
          f"backend={'yes' if backend else 'no'} painted={painted} {time.time() - t0:.1f}s", flush=True)
    return 0 if not problems else 1


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--check" in argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication(argv)   # noqa: F841
        return self_check()
    if not platform_win.single_instance():
        app = QApplication(argv)
        from . import form_kit as F
        F.alert("赛博背背佳已经开着了", "看看屏幕右下角的托盘（小箭头里面），或者桌面上的鲸鱼。")
        return 0
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("赛博背背佳")
    if platform_win.IS_WINDOWS:
        f = app.font()
        f.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", f.family()])
        app.setFont(f)
    host = Host(app)   # noqa: F841 —— 留着引用
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
