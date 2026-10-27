"""照翻 Pet2/Mac/PostureApp.swift：管“体态测试”（PostureTest）这个后台进程：桌宠开着就让它在后台判断，暂停/退出时停掉。

参数和退出码照它 README 的“给桌宠接”：
  --no-preview 后台判断；--calibrate --once 带窗口校准；--selftest --once 带窗口自测；
  退出码 0 正常，1 出错，2 参数错，3 已经有一个在跑，4 摄像头打不开，5 还没校准。
同一时间只能开一个：校准、自测前先停后台那份，结束后再开回来。

Windows 和 Mac 不一样的地方：
- 停：Windows 没有 SIGTERM，点亮后端入口在等的事件（platform_win.request_stop），5 秒还没退再强关。
- 摄像头权限：Mac 问 AVFoundation；Windows 读注册表里的相机开关（关了就等用户去设置里打开）。
- 后台进程放进“作业”：桌宠退出或崩了，它跟着关。
"""
import os
import subprocess
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from pet2win.core import paths
from pet2win.core.backoff import Backoff, PostureExit
from pet2win.core.swiftcompat import split_newlines, trim_whitespaces

from . import platform_win, resources


@dataclass(frozen=True)
class State:
    kind: str           # not_installed / stopped / running / foreground / already_running / needs_calibration /
    text: str = ""      # camera_unavailable / camera_denied / failed


NOT_INSTALLED = State("not_installed")
STOPPED = State("stopped")
RUNNING = State("running")
ALREADY_RUNNING = State("already_running")
NEEDS_CALIBRATION = State("needs_calibration")
CAMERA_UNAVAILABLE = State("camera_unavailable")
CAMERA_DENIED = State("camera_denied")


def foreground(title):
    return State("foreground", title)


def failed(text):
    return State("failed", text)


class PostureApp(QObject):
    _export_done = Signal(object, object)   # (结果, 回调)：从后台线程回到主线程

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = STOPPED
        self.on_change = None          # (State) -> None
        self.on_event = None           # (kind, details) -> None
        self.on_once_finished = None   # (mode, code) -> None：校准 / 自测窗口关了
        self._process = None
        self._process_pid = None
        self._wanted = False           # 桌宠希望它在后台跑
        self._failures = 0
        self._after_stop = None
        self._backoff = Backoff()
        self._launched_at = None
        self._job = platform_win.KillOnCloseJob()
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._launch_background)
        self._watch = QTimer(self)
        self._watch.setInterval(5000)
        self._watch.timeout.connect(self._check_watched)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._force_kill)
        self._export_done.connect(lambda result, done: done(result))

    # ---------- 在哪 ----------

    @property
    def data_directory(self):
        return paths.backend_directory()

    @property
    def running_pid_path(self):
        return os.path.join(self.data_directory, ".running.pid")

    @property
    def run_log_path(self):
        return os.path.join(self.data_directory, "运行记录.txt")

    @property
    def executable(self):
        return resources.backend_executable()

    @property
    def is_calibrated(self):
        return os.path.exists(os.path.join(self.data_directory, "baseline.json"))

    @property
    def log_path(self):
        return os.path.join(self.data_directory, "log.csv")

    def _extra_args(self):
        """数据文件夹不是后端默认的 ~/体态测试数据 时（开发、测试）告诉它。"""
        default = os.path.join(os.path.expanduser("~"), "体态测试数据")
        if os.path.normcase(os.path.abspath(self.data_directory)) != os.path.normcase(os.path.abspath(default)):
            return ["--data-dir", self.data_directory]
        return []

    # ---------- 后台 ----------

    def start_background(self):
        """桌宠开着：让后台那份跑起来（已经在跑就不管）。"""
        self._wanted = True
        self._failures = 0
        self._backoff.reset()
        self._launch_background()

    def retry_now(self):
        """用户点了菜单：退避从头来，马上再试一次（已经在跑就什么都不做）。"""
        if not self._wanted or self._process is not None:
            return
        self._retry.stop()
        self._backoff.reset()
        self._failures = 0
        self._launch_background()

    def _with_camera(self, body):
        """摄像头权限：Windows 设置里没关就直接做；关了就记下状态，等用户去设置里打开。"""
        if platform_win.camera_denied():
            self._set(CAMERA_DENIED)
            self._start_watching()
            return
        body()

    @property
    def camera_authorized(self):
        return not platform_win.camera_denied()

    def open_camera_settings(self):
        platform_win.open_camera_settings()

    def stop_background(self, then=None):
        """暂停 / 退出：停掉后台那份。"""
        self._wanted = False
        self._retry.stop()
        self._stop_watching()
        self._stop(then)

    def run_once(self, mode, title):
        """带窗口跑一次校准或自测：先停后台那份，等它退出，跑完再开回来。"""
        exe = self.executable
        if exe is None:
            self._set(NOT_INSTALLED)
            return
        resume = self._wanted
        self._retry.stop()
        self._backoff.reset()
        self._stop(lambda: self._with_camera(lambda: self._launch_once(mode, title, exe, resume)))

    def _new_process(self, exe, args):
        p = QProcess(self)
        p.setProgram(exe)
        p.setArguments(args + self._extra_args())
        p.setStandardOutputFile(QProcess.nullDevice())
        p.setStandardErrorFile(QProcess.nullDevice())
        return p

    def _launch_once(self, mode, title, exe, resume):
        self._event(f"posture_app_{mode}_started", {})
        p = self._new_process(exe, [f"--{mode}", "--once"])

        def finished(code, status):
            p.deleteLater()
            if p is not self._process:
                return
            self._process = None
            self._process_pid = None
            self._kill_timer.stop()
            code = int(code) if status == QProcess.ExitStatus.NormalExit else -1
            self._event(f"posture_app_{mode}_finished", {"code": str(code)})
            if self.on_once_finished:
                self.on_once_finished(mode, code)
            self._set(ALREADY_RUNNING if code == 3 else STOPPED)
            if self._after_stop is not None:
                after, self._after_stop = self._after_stop, None
                after()
                return
            if resume:
                self._wanted = True
                self._failures = 0
                self._backoff.reset()
                self._launch_background()

        p.finished.connect(finished)
        platform_win.allow_foreground()
        p.start()
        if not p.waitForStarted(5000):
            self._set(failed(f"打不开体态测试：{p.errorString()}"))
            if resume:
                self._wanted = True
                self._launch_background()
            return
        self._process = p
        self._process_pid = p.processId()
        self._job.add(self._process_pid)
        self._set(foreground(title))

    def _launch_background(self):
        if not self._wanted or self._process is not None:
            return
        exe = self.executable
        if exe is None:
            self._set(NOT_INSTALLED)
            return
        if not self.is_calibrated:
            self._set(NEEDS_CALIBRATION)
            return
        # 有一份在跑（用户自己双击开的）：不去撞它，等它关。
        if PostureExit.someone_else_running(self._read_pid_file(), self._is_backend_alive):
            self._set(ALREADY_RUNNING)
            self._start_watching()
            return
        self._with_camera(lambda: self._spawn_background(exe))

    def _read_pid_file(self):
        try:
            with open(self.running_pid_path, "rb") as f:
                return f.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def _is_backend_alive(pid):
        return platform_win.pid_alive(pid, resources.BACKEND_NAMES if platform_win.IS_WINDOWS else None)

    def _spawn_background(self, exe):
        if not self._wanted or self._process is not None:
            return
        self._stop_watching()
        p = self._new_process(exe, ["--no-preview"])
        p.finished.connect(lambda code, status, proc=p: self._background_exited(proc, code, status))
        p.start()
        if not p.waitForStarted(5000):
            self._set(failed(f"打不开体态测试：{p.errorString()}"))
            return
        self._process = p
        self._process_pid = p.processId()
        self._job.add(self._process_pid)
        self._launched_at = time.time()
        self._set(RUNNING)
        self._event("posture_app_started", {"pid": str(self._process_pid)})

    def _background_exited(self, proc, code, status):
        proc.deleteLater()
        if proc is not self._process:
            return
        self._process = None
        self._process_pid = None
        self._kill_timer.stop()
        normal = status == QProcess.ExitStatus.NormalExit
        code = int(code)
        self._event("posture_app_exited", {"code": str(code), "reason": "exit" if normal else "crash"})
        if self._after_stop is not None:
            after, self._after_stop = self._after_stop, None
            self._set(STOPPED)
            after()
            return
        if not self._wanted:
            self._set(STOPPED)
            return
        # 跑了一阵才退的：之前没问题，退避从头算。
        if self._launched_at is not None and time.time() - self._launched_at > 120:
            self._backoff.reset()
        self._launched_at = None
        if normal and code == 0:
            self._set(STOPPED)                       # 自己正常退了（比如用户在别处退出），不硬拉
        elif normal and code == 3:
            # 有一份在跑：只读它写的数据；盯着 .running.pid，它变空了再由桌宠接手。
            self._set(ALREADY_RUNNING)
            self._start_watching()
        elif normal and code == 4:
            if (PostureExit.is_permission_problem(self.run_log_tail()) or platform_win.camera_denied()) \
                    and not self.camera_authorized:
                # 没权限：等用户去设置里打开，不盲目重试。
                self._set(CAMERA_DENIED)
                self._start_watching()
            else:
                # 摄像头被别的软件占着之类：退避重试。
                self._set(CAMERA_UNAVAILABLE)
                self._schedule_retry(self._backoff.next())
        elif normal and code == 5:
            self._set(NEEDS_CALIBRATION)             # 提示先校准，不重试
        elif normal and code == 2:
            self._set(failed("体态测试不认识启动参数（退出码 2）"))
        else:
            self._failures += 1
            if self._failures >= 4:
                self._set(failed("体态测试反复出错，看“体态测试数据\\运行记录.txt”"))
            else:
                self._set(failed(f"体态测试出错了，{10 * self._failures} 秒后再试"))
                self._schedule_retry(10 * self._failures)

    def run_log_tail(self):
        """运行记录.txt 最后一小段。"""
        try:
            with open(self.run_log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(size - 800 if size > 800 else 0)
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def _start_watching(self):
        if not self._watch.isActive():
            self._watch.start()

    def _stop_watching(self):
        self._watch.stop()

    def _check_watched(self):
        """每 5 秒看一眼：别人那份关了没、摄像头权限给了没。变了就马上开。"""
        if not self._wanted or self._process is not None:
            self._stop_watching()
            return
        if self.state.kind == "already_running":
            if not PostureExit.someone_else_running(self._read_pid_file(), self._is_backend_alive):
                self._stop_watching()
                self._event("posture_app_other_closed", {})
                self._launch_background()
        elif self.state.kind == "camera_denied":
            if self.camera_authorized:
                self._stop_watching()
                self._event("camera_permission_granted_later", {})
                self._launch_background()
        else:
            self._stop_watching()

    def _schedule_retry(self, seconds):
        self._retry.stop()
        self._retry.start(int(seconds * 1000))

    def _stop(self, then):
        p = self._process
        if p is None or p.state() == QProcess.ProcessState.NotRunning:
            self._process = None
            self._process_pid = None
            if self.state.kind != "foreground" and self.state == RUNNING:
                self._set(STOPPED)
            if then:
                then()
            return
        self._after_stop = then or (lambda: None)
        # 叫它自己停（它会把还在跟的提醒记成 ended，再退出）；叫不到就让系统关窗口。5 秒还没退就强关。
        if not platform_win.request_stop(self._process_pid):
            p.terminate()
        self._kill_timer.start(5000)

    def _force_kill(self):
        p = self._process
        if p is not None and p.state() != QProcess.ProcessState.NotRunning:
            p.kill()

    def stop_synchronously(self):
        """退出桌宠时同步停掉，最多等 3 秒。"""
        self._wanted = False
        self._retry.stop()
        self._stop_watching()
        p = self._process
        if p is None or p.state() == QProcess.ProcessState.NotRunning:
            return
        self._after_stop = None
        if not platform_win.request_stop(self._process_pid):
            p.terminate()
        if not p.waitForFinished(3000):
            p.kill()
            p.waitForFinished(1000)

    def _set(self, new):
        if new == self.state:
            return
        self.state = new
        if self.on_change:
            self.on_change(new)

    def _event(self, kind, details):
        if self.on_event:
            self.on_event(kind, details)

    @property
    def status_text(self):
        """给脚下小字用；None 表示没什么要说的。"""
        k = self.state.kind
        if k == "not_installed":
            return "没找到“体态测试”：请把压缩包完整解压，不要只拿出桌宠"
        if k == "needs_calibration":
            return "还没校准，点菜单里的“校准…”"
        if k == "camera_unavailable":
            return "摄像头被别的软件占着，过一会儿再试"
        if k == "camera_denied":
            return "没有摄像头权限：设置 → 隐私和安全性 → 相机，打开“允许桌面应用访问相机”"
        if k == "failed":
            return self.state.text
        if k == "foreground":
            return f"{self.state.text}中…"
        return None

    # ---------- 导出 ----------

    def export(self, with_pain, with_points, done):
        """--export：不开摄像头，把数据打成 zip 放进 ~/体态测试数据/导出/。后台那份开着也能用。
        成功：退出码 0，最后一行是 zip 路径。没导出：退出码 1，打一行“没导出：原因”。在后台线程跑，结果回到主线程。
        done(("ok", 路径) 或 ("error", 原因))。"""
        exe = self.executable
        if exe is None:
            done(("error", "没找到“体态测试”"))
            return
        args = ["--export"]
        if with_pain:
            args.append("--with-pain")
        if with_points:
            args.append("--with-points")
        args += self._extra_args()
        self._event("export_started", {"with_pain": "1" if with_pain else "0", "with_points": "1" if with_points else "0"})

        def work():
            text, code = "", -1
            try:
                res = subprocess.run([exe] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=platform_win.no_window_flags(), timeout=600)
                text = res.stdout.decode("utf-8", errors="replace")
                code = res.returncode
            except (OSError, subprocess.SubprocessError) as e:
                text = f"没导出：打不开体态测试（{e}）"
            lines = [x for x in (trim_whitespaces(l) for l in split_newlines(text)) if x]
            if code == 0 and lines and lines[-1].endswith(".zip") and os.path.exists(lines[-1]):
                result = ("ok", lines[-1])
            else:
                reason = next((l for l in reversed(lines) if l.startswith("没导出")), None) or (lines[-1] if lines else None) \
                    or f"没导出：体态测试退出码 {code}"
                result = ("error", reason if reason.startswith("没导出") else f"没导出：{reason}")
            self._export_done.emit(result, finish)

        def finish(result):
            if result[0] == "ok":
                self._event("export_finished", {"file": os.path.basename(result[1])})
            else:
                self._event("export_failed", {"reason": result[1]})
            done(result)

        threading.Thread(target=work, name="export", daemon=True).start()

    def check(self, done):
        """--check：不开摄像头，只看模型和字体能不能用（引导第 4 步）。done(退出码, 最后一行)，回到主线程。"""
        exe = self.executable
        if exe is None:
            done(None, "")
            return

        def work():
            try:
                res = subprocess.run([exe, "--check"] + self._extra_args(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=platform_win.no_window_flags(), timeout=120)
                out = res.stdout.decode("utf-8", errors="replace")
                code = res.returncode
            except (OSError, subprocess.SubprocessError) as e:
                out, code = str(e), -1
            lines = split_newlines(out)
            self._export_done.emit((code, lines[-1] if lines else ""), lambda r: done(*r))

        threading.Thread(target=work, name="check", daemon=True).start()
