"""测试用的小工具：和 Pet2/Tests/main.swift 开头那几个一样（check、Recorder、feed、pattern）。"""
import inspect
import math
import sys

from pet2win.core.brain import Brain, Drifting, Reminding, Rewarding, Settings
from pet2win.core.posture_input import Issue, PostureRow

failures = 0
checks = 0


class Fatal(Exception):
    """Swift 的 fatalError：这一项后面没法再测，整个停下。"""


def check(condition, message):
    global failures, checks
    checks += 1
    if not condition:
        failures += 1
        frame = inspect.stack()[1]
        print(f"✗ {frame.filename.rsplit('/', 1)[-1]} 第 {frame.lineno} 行：{message}")


def fatal(message):
    global failures
    failures += 1
    raise Fatal(message)


class Recorder:
    def __init__(self):
        self.events = []

    def attach(self, brain):
        brain.on_event = lambda kind, details, _t: self.events.append((kind, dict(details)))

    def count(self, kind):
        return sum(1 for k, _ in self.events if k == kind)

    def has(self, kind, key, value):
        return any(k == kind and d.get(key) == value for k, d in self.events)


def is_reminding(s):
    return isinstance(s, Reminding)


def is_rewarding(s):
    return isinstance(s, Rewarding)


def is_drifting(s, issue):
    return isinstance(s, Drifting) and s.issue == issue


def feed(brain, t0, seconds, issues, step=2.0, tilt=None):
    """每 2 秒一行，和后端测试参数差不多。返回最后的时间。"""
    t = t0
    while t < t0 + seconds:
        brain.ingest(PostureRow.make(t, issues, tilt_to_left=tilt), now=t)
        brain.tick(now=t)
        t += step
    return t


BASE = 1_793_000_000.0


def quiet_settings():
    """正式参数，但不提久坐、“坐直”也先不提（各自单测）。"""
    s = Settings()
    s.sitting_minutes = 9999
    s.upright_rule.share = 2
    return s


def pattern(brain, t0, seconds, issue, fraction, step, side_value=None, eye_drop=None):
    """按比例出现某个姿势：每行要么有它、要么坐直，均匀分布。返回最后的时间。"""
    t = t0
    k = 0.0
    while t < t0 + seconds:
        has = math.floor((k + 1) * fraction) > math.floor(k * fraction)
        row = PostureRow.make(t, [issue] if has else [])
        # 弯腰要眼睛低够（eye_drop ≥ 0.4）才算；没给就按真弯腰 0.5
        if has and issue == Issue.SLOUCH:
            row.eye_drop = eye_drop if eye_drop is not None else 0.5
        if has and side_value is not None:
            if issue == Issue.HEAD_TILT:
                row.tilt_to_left = side_value
            elif issue == Issue.HEAD_TURN:
                row.turn_to_left = side_value
            elif issue == Issue.UNEVEN_SHOULDERS:
                row.left_shoulder_up = side_value
        brain.ingest(row, now=t)
        brain.tick(now=t)
        t += step
        k += 1
    return t
