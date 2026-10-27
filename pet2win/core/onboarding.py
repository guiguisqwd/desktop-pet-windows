"""照翻 Pet2/Core/Onboarding.swift：首次引导走到哪一步。只用标准库。"""
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

from .codable import DecodeError, opt_number, req_bool, req_dict, req_int, without_none


class Step(IntEnum):
    WELCOME = 1
    CONSENT = 2
    PROFILE = 3
    PLACEMENT = 4
    CALIBRATE = 5
    DIRECTION = 6
    MEET_PET = 7
    SELFTEST = 8
    FINISH = 9

    @property
    def title(self):
        return _TITLES[self]

    @property
    def skippable(self):
        """第 7、8 步可以跳过。"""
        return self in (Step.MEET_PET, Step.SELFTEST)

    @property
    def next(self):
        return Step(self.value + 1) if self.value < 9 else None

    @property
    def previous(self):
        return Step(self.value - 1) if self.value > 1 else None


_TITLES = {Step.WELCOME: "欢迎", Step.CONSENT: "同意", Step.PROFILE: "基本资料", Step.PLACEMENT: "摆放",
           Step.CALIBRATE: "校准", Step.DIRECTION: "方向检查", Step.MEET_PET: "认识桌宠", Step.SELFTEST: "摸底测试",
           Step.FINISH: "完成"}

SELFTEST_REMINDER_DELAY = 24 * 3600


@dataclass
class OnboardingState:
    """onboarding.json 没 done 时，启动先走引导；中途退出下次从 step 接着走。"""
    step: Step = Step.WELCOME
    done: bool = False
    # 摸底测试选了“以后再做”的时间；24 小时后气泡问一次，只问一次。
    selftest_deferred_at: Optional[float] = None
    selftest_reminded: bool = False
    updated_at: Optional[float] = None

    selftest_reminder_delay = SELFTEST_REMINDER_DELAY

    def should_remind_selftest(self, now):
        """现在该不该在气泡里问一次摸底测试。"""
        if not self.done or self.selftest_reminded or self.selftest_deferred_at is None:
            return False
        return now - self.selftest_deferred_at >= SELFTEST_REMINDER_DELAY

    def to_json(self):
        return without_none({"step": int(self.step), "done": self.done, "selftest_deferred_at": self.selftest_deferred_at,
                             "selftest_reminded": self.selftest_reminded, "updated_at": self.updated_at})

    @classmethod
    def from_json(cls, obj):
        obj = req_dict(obj)
        raw = req_int(obj, "step")
        if raw < 1 or raw > 9:
            raise DecodeError("step")
        return cls(step=Step(raw), done=req_bool(obj, "done"), selftest_deferred_at=opt_number(obj, "selftest_deferred_at"),
                   selftest_reminded=req_bool(obj, "selftest_reminded"), updated_at=opt_number(obj, "updated_at"))
