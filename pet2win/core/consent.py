"""照翻 Pet2/Core/Consent.swift：同意书第 2 版的勾选（①–⑥）和规则。只用标准库。
c1 使用产品（必选）c2 研究 c3 健康信息 c4 原始关键点 c5 可以联系 c6 跨境（中国地区，第一阶段不开放）。"""
import dataclasses
from dataclasses import dataclass, field
from typing import Optional

from .codable import DecodeError, req_bool, req_dict, req_number, req_str

VERSION = "2"


@dataclass
class ConsentChoice:
    time: float
    adult: bool
    c1: bool
    c2: bool = False
    c3: bool = False
    c4: bool = False
    c5: bool = False
    c6: bool = False
    version: str = VERSION
    lang: str = "zh"

    def to_json(self):
        return {"time": self.time, "version": self.version, "lang": self.lang, "adult": self.adult,
                "c1": self.c1, "c2": self.c2, "c3": self.c3, "c4": self.c4, "c5": self.c5, "c6": self.c6}

    @classmethod
    def from_json(cls, obj):
        obj = req_dict(obj)
        return cls(time=req_number(obj, "time"), version=req_str(obj, "version"), lang=req_str(obj, "lang"),
                   adult=req_bool(obj, "adult"), c1=req_bool(obj, "c1"), c2=req_bool(obj, "c2"),
                   c3=req_bool(obj, "c3"), c4=req_bool(obj, "c4"), c5=req_bool(obj, "c5"), c6=req_bool(obj, "c6"))

    def copy(self):
        return dataclasses.replace(self)


@dataclass
class ConsentRecord:
    """consent.json：{"version":"2","current":{...},"history":[...]}。"""
    version: str = VERSION
    current: Optional[ConsentChoice] = None
    history: list = field(default_factory=list)

    def to_json(self):
        out = {"version": self.version, "history": [c.to_json() for c in self.history]}
        if self.current is not None:
            out["current"] = self.current.to_json()
        return out

    @classmethod
    def from_json(cls, obj):
        obj = req_dict(obj)
        history = obj.get("history")
        if not isinstance(history, list):
            raise DecodeError("history")
        current = obj.get("current")
        return cls(version=req_str(obj, "version"),
                   current=ConsentChoice.from_json(current) if current is not None else None,
                   history=[ConsentChoice.from_json(c) for c in history])


class Consent:
    version = VERSION

    @staticmethod
    def cross_border_available(country):
        """地区为中国时 ⑥ 灰掉（第一阶段不开放）。其余地区 ⑥ 不适用，也不能勾。所以第一阶段 ⑥ 总是 False。"""
        return False

    @staticmethod
    def normalized(choice, country):
        """按规则修正一次勾选：未满 18 只能 ①；⑥ 现在不开放；② 没勾时 ③④ 没有意义。"""
        c = choice.copy()
        c.version = VERSION
        if not c.adult:
            c.c2 = c.c3 = c.c4 = c.c5 = c.c6 = False
        if not Consent.cross_border_available(country):
            c.c6 = False
        if not c.c2:
            c.c3 = c.c4 = c.c6 = False
        return c

    @staticmethod
    def can_use(choice):
        """① 必选：没勾就不能继续用。"""
        return choice is not None and choice.c1 is True

    @staticmethod
    def can_export(choice):
        """导出给 gui 需要 ①② 都勾了、成年。"""
        if choice is None:
            return False
        return bool(choice.c1 and choice.c2 and choice.adult)

    @staticmethod
    def asks_pain(choice):
        return Consent.can_export(choice) and choice.c3 is True

    @staticmethod
    def asks_points(choice):
        return Consent.can_export(choice) and choice.c4 is True

    @staticmethod
    def export_arguments(choice, want_pain, want_points):
        """后端参数。用户在导出那一步还能选不带；没同意的永远不加。"""
        if not Consent.can_export(choice):
            return None
        args = ["--export"]
        if want_pain and Consent.asks_pain(choice):
            args.append("--with-pain")
        if want_points and Consent.asks_points(choice):
            args.append("--with-points")
        return args

    @staticmethod
    def record(choice, country, record):
        """改勾选：追加 history，current = 最新一条。返回撤回了 ② 没有（要提示把编号告诉 gui）。"""
        c = Consent.normalized(choice, country)
        withdrew_research = record.current is not None and record.current.c2 is True and not c.c2
        record.version = VERSION
        record.history.append(c)
        record.current = c
        return withdrew_research
