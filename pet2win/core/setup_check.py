"""照翻 Pet2/Core/SetupCheck.swift：setup.json 和方向检查。只用标准库。"""
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .codable import opt_bool, opt_number, req_dict, req_str, without_none
from .csv_tail import CSVTail
from .fileio import open_shared
from .posture_input import PostureRow
from .swiftcompat import first_lone_newline, split_newlines, swift_double, swift_sorted, trim_whitespaces


@dataclass
class SetupInfo:
    """setup.json：摄像头和摆放、方向检查的结果。"""
    camera: str = "builtin"      # builtin / external
    placement: str = "desk"      # desk / stand
    mirrored: Optional[bool] = None
    shoulders_visible: Optional[bool] = None
    checked_at: Optional[float] = None

    def to_json(self):
        return without_none({"camera": self.camera, "placement": self.placement, "mirrored": self.mirrored,
                             "shoulders_visible": self.shoulders_visible, "checked_at": self.checked_at})

    @classmethod
    def from_json(cls, obj):
        obj = req_dict(obj)
        return cls(camera=req_str(obj, "camera"), placement=req_str(obj, "placement"),
                   mirrored=opt_bool(obj, "mirrored"), shoulders_visible=opt_bool(obj, "shoulders_visible"),
                   checked_at=opt_number(obj, "checked_at"))


class Mirror(Enum):
    NOT_MIRRORED = "notMirrored"   # 值和 Swift 的 "\(r.mirror)" 一样，事件里记的就是它
    MIRRORED = "mirrored"
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class Result:
    mirror: Mirror
    shoulders_visible: Optional[bool]   # None：这段时间一行都没有
    rows: int


class DirectionCheck:
    """方向检查：用户往自己的左边歪头 3 秒，看后端写的 tilt_to_left_deg（正数 = 本人的左边）。"""
    threshold = 3.0
    default_frame_height = 1080.0
    # 肩膀 y 到画面高度的 97% 以下才算看得到（再往下就是贴着画面底边，多半被切掉了）。
    bottom_share = 0.97

    @staticmethod
    def median(values):
        if not values:
            return None
        s = swift_sorted(values)
        n = len(s)
        return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2

    @staticmethod
    def mirror(tilts):
        m = DirectionCheck.median(tilts)
        if m is None:
            return Mirror.UNCLEAR
        if m > DirectionCheck.threshold:
            return Mirror.NOT_MIRRORED
        if m < -DirectionCheck.threshold:
            return Mirror.MIRRORED
        return Mirror.UNCLEAR

    @staticmethod
    def shoulders_visible(left_y, right_y, frame_height):
        """一行肩膀看不看得到：两个 y 都有、都在底边 97% 以上。"""
        if left_y is None or right_y is None:
            return False
        limit = (frame_height if frame_height is not None else DirectionCheck.default_frame_height) * DirectionCheck.bottom_share
        return left_y < limit and right_y < limit

    @staticmethod
    def evaluate(text, since, until=float("inf"), frame_height=None):
        """从 log.csv 的内容（带表头）里取 since 之后的行来判断。按表头列名读。肩膀：多数行看得到才算看得到。"""
        lines = split_newlines(text)
        if not lines or "time" not in CSVTail.split(lines[0]):
            return Result(Mirror.UNCLEAR, None, 0)
        header = CSVTail.split(lines[0])

        def col(name):
            return header.index(name) if name in header else None

        i_time = col("time")
        i_tilt, i_l, i_r = col("tilt_to_left_deg"), col("l_sh_y"), col("r_sh_y")
        tilts = []
        visible = rows = 0
        for line in lines[1:]:
            f = CSVTail.split(line)

            def num(i):
                if i is None or i >= len(f):
                    return None
                return swift_double(trim_whitespaces(f[i]))

            t = num(i_time)
            if t is None or not (t >= since and t <= until):
                continue
            rows += 1
            tilt = num(i_tilt)
            if tilt is not None:
                tilts.append(tilt)
            if DirectionCheck.shoulders_visible(num(i_l), num(i_r), frame_height):
                visible += 1
        return Result(DirectionCheck.mirror(tilts), None if rows == 0 else visible * 2 > rows, rows)

    @staticmethod
    def rows(text, since):
        """since 之后的行（按表头列名解析）。引导里“认识桌宠”用它看用户有没有低头。"""
        lines = split_newlines(text)
        if not lines:
            return []
        header = CSVTail.split(lines[0])
        out = []
        for line in lines[1:]:
            f = CSVTail.split(line)
            d = {}
            for i, name in enumerate(header):
                if i < len(f):
                    d[name] = f[i]
            row = PostureRow.from_csv(d)
            if row is None or not (row.t >= since):
                continue
            out.append(row)
        return out

    @staticmethod
    def read_tail(path, nbytes=256 * 1024):
        """读一个可能很大的 log.csv：表头 + 末尾一段。打不开返回 None。"""
        try:
            h = open_shared(path)
        except OSError:
            return None
        with h:
            try:
                head_bytes = h.read(8192)
            except OSError:
                head_bytes = b""
            try:
                head = head_bytes.decode("utf-8")
            except UnicodeDecodeError:
                head = ""
            first = split_newlines(head)
            header = first[0] if first else ""
            try:
                size = h.seek(0, os.SEEK_END)
            except OSError:
                size = 0
            start = size - nbytes if size > nbytes else 0
            try:
                h.seek(start)
                data = h.read()
            except OSError:
                data = b""
        tail = data.decode("utf-8", errors="replace")
        if start > 0:
            nl = first_lone_newline(tail)
            if nl is not None:
                tail = tail[nl + 1:]
        return header + "\n" + tail if start > 0 else tail
