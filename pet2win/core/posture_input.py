"""照翻 Pet2/Core/PostureInput.swift：体态软件写的 log.csv / log-alerts.csv 的一行。只用标准库。"""
import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .swiftcompat import swift_double, swift_int, trim_whitespaces


class Issue(Enum):
    """体态软件（posture_cam.py）判出的姿势。名字和它写进 log.csv 的一样。顺序和 Swift 的 allCases 一样（提醒时按这个顺序轮）。"""
    FORWARD_HEAD = "forward_head"
    HEAD_DOWN = "head_down"
    SHRUG = "shrug"
    HEAD_TILT = "head_tilt"
    UNEVEN_SHOULDERS = "uneven_shoulders"
    HEAD_TURN = "head_turn"
    # 2026-10-27.2 的后端新加：
    SLOUCH = "slouch"          # 弯腰：头整体下沉、头没变小
    LEAN_BACK = "lean_back"    # 往后靠：头变小、眼睛没怎么低
    SLIDE = "slide"            # 滑坐：头变小、眼睛低很多

    @property
    def cn(self):
        """Swift 里的 issue.name（中文名）。"""
        return _ISSUE_NAMES[self]

    @classmethod
    def from_raw(cls, raw):
        """Issue(rawValue:)：不认就是 None。"""
        try:
            return cls(raw)
        except ValueError:
            return None


_ISSUE_NAMES = {
    Issue.FORWARD_HEAD: "头前伸", Issue.HEAD_DOWN: "低头", Issue.SHRUG: "耸肩", Issue.HEAD_TILT: "歪头",
    Issue.UNEVEN_SHOULDERS: "高低肩", Issue.HEAD_TURN: "扭头", Issue.SLOUCH: "弯腰", Issue.LEAN_BACK: "往后靠",
    Issue.SLIDE: "滑坐",
}

ALL_ISSUES = list(Issue)


class Side(Enum):
    """本人的左右（和后端一致）。"""
    LEFT = "left"
    RIGHT = "right"


@dataclass
class PostureRow:
    """log.csv 的一行：一次判断。没校准、看不到人时后端不写行。

    Swift 里是 struct（值类型）：Brain 收进去时会复制一份，外面再改不影响。"""
    t: float
    issues: list
    severity: float = 0.7
    tilt_to_left: Optional[float] = None       # 头往本人左边歪了几度（负数是往右）
    turn_to_left: Optional[float] = None       # 头往本人左边转了多少（负数是往右）
    left_shoulder_up: Optional[float] = None   # 左肩比校准时高几度（负数是右肩高）
    persons: Optional[int] = None              # 画面里有几个人（2026-10-27 的后端才有这一列）
    eye_drop: Optional[float] = None           # 眼睛比校准时低了几个耳距（2026-10-27.2 起）
    judged: bool = True                        # 这一行后端真的判断了（多人行只写时间和人数）

    @classmethod
    def make(cls, t, issues, severity=0.7, tilt_to_left=None, turn_to_left=None, left_shoulder_up=None,
             persons=None, judged=True, eye_drop=None):
        """Swift 的 init(t:issues:severity:tiltToLeft:turnToLeft:leftShoulderUp:persons:judged:)。
        eyeDrop 在 Swift 里是之后再赋值，这里顺手给个参数。"""
        return cls(t=t, issues=list(issues), severity=severity, tilt_to_left=tilt_to_left, turn_to_left=turn_to_left,
                   left_shoulder_up=left_shoulder_up, persons=persons, eye_drop=eye_drop,
                   judged=judged and (persons if persons is not None else 1) <= 1)

    @classmethod
    def from_csv(cls, row):
        """init?(csv:)：按列名读。时间读不出来就返回 None。"""
        raw_t = row.get("time")
        t = swift_double(trim_whitespaces(raw_t)) if raw_t is not None else None
        if t is None or t != t or t in (float("inf"), float("-inf")):
            return None
        issues = []
        for part in (row.get("issues") or "").split("|"):
            if part == "":
                continue  # Swift 的 split 不要空段
            issue = Issue.from_raw(trim_whitespaces(part))
            if issue is not None:
                issues.append(issue)
        severity_text = trim_whitespaces(row["severity"]) if row.get("severity") is not None else ""
        severity = swift_double(severity_text)
        persons_raw = row.get("persons")
        persons = swift_int(trim_whitespaces(persons_raw)) if persons_raw is not None else None
        # 旧版日志没有 persons 列：按一个人算。severity 空着就是后端这一刻没判断。
        judged = severity_text != "" and (persons if persons is not None else 1) <= 1

        def num(key, trim=False):
            v = row.get(key)
            if v is None:
                return None
            return swift_double(trim_whitespaces(v) if trim else v)

        return cls(t=t, issues=issues, severity=severity if severity is not None else 0.0,
                   tilt_to_left=num("tilt_to_left_deg"), turn_to_left=num("turn_to_left"),
                   left_shoulder_up=num("left_shoulder_up_deg"), persons=persons,
                   eye_drop=num("eye_drop", trim=True), judged=judged)

    def side(self, issue):
        """报出来的问题往哪边。只有歪头、扭头、高低肩有方向。"""
        if issue == Issue.HEAD_TILT:
            value = self.tilt_to_left
        elif issue == Issue.HEAD_TURN:
            value = self.turn_to_left
        elif issue == Issue.UNEVEN_SHOULDERS:
            value = self.left_shoulder_up
        else:
            value = None
        if value is None or value == 0:
            return None
        return Side.LEFT if value > 0 else Side.RIGHT

    def copy(self):
        return dataclasses.replace(self, issues=list(self.issues))


class AlertKind(Enum):
    POSTURE = "posture"
    SITTING = "sitting"


class AlertOutcome(Enum):
    CORRECTED = "corrected"
    LEFT_SEAT = "left_seat"
    STOOD_UP = "stood_up"
    NOT_CORRECTED = "not_corrected"
    INTERRUPTED = "interrupted"
    ENDED = "ended"


@dataclass
class AlertRow:
    """log-alerts.csv 的一行：后端的一次提醒和它的结果。后端只在有结果时才写这一行。"""
    alert_time: float
    kind: AlertKind
    issue: Optional[Issue]
    outcome: AlertOutcome
    outcome_time: float

    @classmethod
    def from_csv(cls, row):
        t = swift_double(row["time"]) if row.get("time") is not None else None
        kind = _enum_or_none(AlertKind, row.get("kind"))
        outcome = _enum_or_none(AlertOutcome, row.get("outcome"))
        if t is None or kind is None or outcome is None:
            return None
        issue = Issue.from_raw(row["issue"]) if row.get("issue") is not None else None
        ot = swift_double(row["outcome_time"]) if row.get("outcome_time") is not None else None
        return cls(alert_time=t, kind=kind, issue=issue, outcome=outcome, outcome_time=ot if ot is not None else t)


def _enum_or_none(enum, raw):
    if raw is None:
        return None
    try:
        return enum(raw)
    except ValueError:
        return None
