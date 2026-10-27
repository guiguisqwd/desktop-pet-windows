"""照翻 Pet2/Core/Backoff.swift。只用标准库。"""
from .swiftcompat import swift_int, trim_whitespaces_and_newlines


class Backoff:
    """后台体态测试没起来时，隔多久再试：30 秒 → 1 分 → 2 分 → 4 分 → 8 分 → 之后一直 10 分。"""
    steps = [30, 60, 120, 240, 480, 600]

    def __init__(self):
        self.attempt = 0

    def next(self):
        """下一次要等多久，并记一次。"""
        delay = self.steps[min(self.attempt, len(self.steps) - 1)]
        self.attempt += 1
        return delay

    def reset(self):
        self.attempt = 0


class PostureExit:
    """看后台体态测试的退出情况，决定下一步怎么办。"""

    @staticmethod
    def is_permission_problem(log_tail):
        """运行记录.txt 的最后几行：是不是因为没有摄像头权限才打不开（而不是被别的软件占着）。"""
        text = log_tail.lower()
        return ("not authorized" in text or "没有权限" in text or "没权限" in text
                or "允许使用摄像头" in text or "等你允许" in text)

    @staticmethod
    def someone_else_running(pid_file_contents, is_alive):
        """.running.pid 的内容：空的、没有这个文件、或者那个进程已经不在了，就是没人在跑。"""
        if pid_file_contents is None:
            return False
        text = trim_whitespaces_and_newlines(pid_file_contents)
        if not text:
            return False
        pid = swift_int(text, bits=32)
        if pid is None or pid <= 0:
            return False
        return bool(is_alive(pid))
