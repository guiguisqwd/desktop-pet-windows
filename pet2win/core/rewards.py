"""照翻 Pet2/Core/Rewards.swift：奖励放哪个 GIF。只用标准库。

什么时候放哪个（从上往下，先对上的算）：
  1. 久坐提醒后离开、又回到画面里       → 托腮
  2. 今天第 1 次改过来，或第 5、10、15… 次 → 送爱心
  3. 提醒后很快就改过来                  → 点赞
  4. 其余的改过来                        → +1
"""
import time
from dataclasses import dataclass, field
from enum import Enum


class RewardKind(Enum):
    """名字和素材文件夹一样：Assets/whale/<名字>/<名字>_<部分>.gif。"""
    HEART = "heart"            # 送爱心
    THUMBS_UP = "thumbs_up"    # 点赞
    PLUS1 = "plus1"            # +1
    CHIN_REST = "chin_rest"    # 托腮


ALL_REWARD_KINDS = list(RewardKind)


def _default_seconds():
    return {RewardKind.HEART: 9.9, RewardKind.THUMBS_UP: 7.9, RewardKind.PLUS1: 7.8, RewardKind.CHIN_REST: 7.9}


@dataclass
class RewardRules:
    # 提醒弹出后多少秒内改过来算“快”（30 秒内动了 + 10 秒确认）。
    fast_fix_seconds: float = 40
    # 每改过来这么多次送一次爱心（第 1 次也送）。0 = 只有第 1 次送。
    heart_every: int = 5
    # 每种奖励整段演多久。桌宠启动时按 GIF 的实际长度填，这里是没填时的默认。
    seconds: dict = field(default_factory=_default_seconds)

    def pick(self, issue, seconds_since_reminder, today):
        """issue 为 None：久坐回来的奖励。today：算上这一次，今天第几次改过来。"""
        if issue is None:
            return RewardKind.CHIN_REST
        if today <= 1 or (self.heart_every > 0 and today % self.heart_every == 0):
            return RewardKind.HEART
        if seconds_since_reminder is not None and seconds_since_reminder <= self.fast_fix_seconds:
            return RewardKind.THUMBS_UP
        return RewardKind.PLUS1

    @staticmethod
    def line(kind, issue, today):
        """气泡里那句话。"""
        from .brain import Lines
        if issue is None:
            return Lines.sitting_reward
        if kind == RewardKind.HEART:
            return Lines.reward(issue) if today <= 1 else f"今天第 {today} 次改过来啦，送你一颗心～"
        if kind == RewardKind.THUMBS_UP:
            return "好快！" + Lines.reward(issue)
        if kind == RewardKind.PLUS1:
            return f"+1！今天第 {today} 次改过来啦～"
        return Lines.reward(issue)


@dataclass
class CorrectionCounter:
    """今天改过来几次：按本机的日期，过了零点清零；桌宠重启也从 0 开始（不读旧记录）。"""
    day: int = 0
    count: int = 0

    def add(self, t):
        """记一次，返回算上这次今天第几次。"""
        key = self.day_key(t)
        if key != self.day:
            self.day = key
            self.count = 0
        self.count += 1
        return self.count

    @staticmethod
    def day_key(t):
        lt = time.localtime(t)
        return lt.tm_year * 10000 + lt.tm_mon * 100 + lt.tm_mday
