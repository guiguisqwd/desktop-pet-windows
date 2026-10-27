"""照翻 Pet2/Core/Brain.swift：桌宠的全部决定都在这里。只用标准库。

输入：体态软件写的 log.csv（每次判断一行，里面的 issues 是后端判出的姿势），加上用户的“稍后”“暂停”。
log-alerts.csv（后端每次提醒的结果）只记下来对照，不触发任何东西。输出：现在该演哪一幕（Scene），以及要记下来的事件。

一条最小的奖励闭环：
  出现某个姿势 → 演它的“下一步”（不出声）
  → 同一姿势在它自己的窗口里占到门槛 → 气泡说是哪个姿势、多久了
  → 那个姿势连续 10 秒没再出现 → 伸懒腰、爱心 + 一句回应 → 回到平常（5 分钟没改过来就安静收尾）
没改过来就安静收尾，不责备、不升级；点“稍后”就真的安静 10 分钟，期间的提醒全部吞掉、之后不补发。

和 Swift 版逐行对应（函数名换成 snake_case）。改这里之前先改 Mac 版，再照着改，然后跑 difftest 对拍。
"""
import copy
import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .posture_input import ALL_ISSUES, Issue, Side
from .rewards import CorrectionCounter, RewardKind, RewardRules
from .swiftcompat import fmt2, swift_max, swift_min, swift_rounded


@dataclass
class Rule:
    """一种姿势的提醒门槛：过去 window 秒里占到 share；same_side 要求多数行是同一个方向。"""
    window: float
    share: float
    same_side: bool = False


def _default_rules():
    # 每种姿势分开设（gui 2026-10-27 同意的姿势清单第 6 节）。
    return {
        Issue.FORWARD_HEAD: Rule(300, 0.7),
        Issue.HEAD_DOWN: Rule(300, 0.8),
        Issue.SHRUG: Rule(180, 0.7),
        Issue.HEAD_TILT: Rule(300, 0.7, same_side=True),
        Issue.UNEVEN_SHOULDERS: Rule(600, 0.8, same_side=True),
        Issue.HEAD_TURN: Rule(600, 0.8, same_side=True),
        # 草稿值（2026-10-27.2 新加的三种，等数据再调）：
        Issue.SLOUCH: Rule(300, 0.7),
        Issue.SLIDE: Rule(300, 0.7),
        Issue.LEAN_BACK: Rule(600, 0.8),
    }


@dataclass
class Settings:
    rules: dict = field(default_factory=_default_rules)
    # “坐直”也算一种姿势：一直坐着不动太久也提醒换个坐姿。
    upright_rule: Rule = field(default_factory=lambda: Rule(1200, 0.8))
    # 窗口要攒满才算：真正判断过的行覆盖到窗口时长的这么多才开始算。
    window_coverage: float = 0.8
    # 两行之间隔太久（没看到人、多人）不算覆盖，每段最多算这么多秒。
    coverage_gap_cap: float = 5
    # 弯腰分轻重：后端报 slouch、但 eye_drop 不到这个数的是“轻弯腰”，桌宠不算弯腰、不提醒，并且这种行算“坐直”。
    slouch_remind_eye_drop: float = 0.4
    # 降到这个占比以下才能再提同一种姿势。
    rearm_share: float = 0.3
    # “坐直”的提醒不等结果：气泡显示这么久就安静收尾。
    upright_bubble_seconds: float = 30
    sitting_minutes: float = 45
    # 这么久没有新行，就当没看到人。
    away_after: float = 10
    # 连续这么久没看到人，算离开了座位。
    left_seat_after: float = 60
    # 两行之间隔这么久，连坐时间重新算。
    sitting_reset_after: float = 120
    # 提醒之后，那个姿势连续这么久没再出现，就算改过来了。
    corrected_after: float = 10
    posture_follow: float = 300
    sitting_follow: float = 600
    snooze_seconds: float = 600
    # 所有姿势加起来每小时最多几次。
    max_reminders_per_hour: int = 2
    # 同一姿势距上次提醒至少这么久。
    same_issue_gap: float = 20 * 60
    # 任意两次提醒之间至少隔这么久。
    min_gap_between_reminders: float = 10 * 60
    # 一次提醒结束后，至少安静这么久才提下一件事。
    quiet_after_reminder: float = 5 * 60
    # 奖励整段演多久，由画面层按 GIF 长度填。rewards.seconds 里没有的种类用它。
    reward_seconds: float = 8
    # 奖励放哪个 GIF、每种演多久。
    rewards: RewardRules = field(default_factory=RewardRules)

    def rule(self, issue):
        r = self.rules.get(issue)
        return r if r is not None else Rule(300, 0.7)

    def set_all_windows(self, window, upright=None):
        """所有窗口设成同一个长度（测试和快速演示用）。"""
        for issue in ALL_ISSUES:
            if issue in self.rules:
                self.rules[issue].window = window
        self.upright_rule.window = upright if upright is not None else window * 2

    @classmethod
    def demo(cls):
        """快速演示：每种姿势都只看 20 秒（坐直看 40 秒），坐 2 分钟就提醒起身。"""
        s = cls()
        s.set_all_windows(20)
        s.min_gap_between_reminders = 0
        s.sitting_minutes = 2
        s.max_reminders_per_hour = 60
        s.same_issue_gap = 30
        s.quiet_after_reminder = 30
        s.sitting_follow = 300
        return s


class ReminderKind(Enum):
    POSTURE = "posture"
    SITTING = "sitting"
    UPRIGHT = "upright"


@dataclass(frozen=True)
class Reminder:
    id: int
    kind: ReminderKind
    issue: Optional[Issue]
    side: Optional[Side]
    line: str
    started_at: float
    # 用户点了“知道了”：气泡收起，但还在等结果。
    bubble_hidden: bool = False


@dataclass(frozen=True)
class Reward:
    id: int
    issue: Optional[Issue]
    line: str
    started_at: float
    # 放哪个 GIF、今天第几次改过来（久坐回来是 0）、整段演多久。
    kind: RewardKind = RewardKind.HEART
    today: int = 0
    seconds: float = 8


# ---------- Scene：现在该演哪一幕 ----------

@dataclass(frozen=True)
class Waiting:
    """还没收到体态数据"""
    text: str


@dataclass(frozen=True)
class Away:
    """没看到人"""


@dataclass(frozen=True)
class Calm:
    """平常"""


@dataclass(frozen=True)
class Drifting:
    """出现问题：演“下一步”，不出声"""
    issue: Issue
    side: Optional[Side]
    intensity: float


@dataclass(frozen=True)
class Reminding:
    reminder: Reminder


@dataclass(frozen=True)
class Rewarding:
    reward: Reward


@dataclass(frozen=True)
class Snoozed:
    until: float


@dataclass(frozen=True)
class Paused:
    pass


CROWDED_TEXT = "画面里不止一个人，先不判断"


class Brain:
    crowded_text = CROWDED_TEXT

    def __init__(self, settings=None):
        # Swift 的 Settings 是值类型：这里复制一份，外面再改不影响。
        self.settings = copy.deepcopy(settings) if settings is not None else Settings()
        # 事件：种类 + 细节 + 时间。
        self.on_event = None
        # 读文件那一层的状态，没数据时显示给用户看。
        self.input_status = "还没收到体态数据"

        self.paused = False
        self.snooze_until = None
        self.reminder = None
        self.reward = None
        self.latest_row = None
        self.last_row_received_at = None
        # 最近一次收到“画面里不止一个人”的行。
        self.last_crowded_at = None

        self._history = []          # [(t, frozenset(issues), {issue: side})]
        self._armed = {}
        self._last_reminder_at = {}
        self._reminder_times = []
        self._ok_since = None
        self._last_reminder_closed_at = None
        self._sitting_start = None
        self._sitting_armed = True
        self._pending_sitting_reward = False
        self._next_id = 1
        self._corrections = CorrectionCounter()   # 今天改过来几次（选奖励用）
        self._last_time = 0.0                     # 最近一次收到输入的时间（关闭提醒时记下来）

    @property
    def _last_seen_at(self):
        """最近一次看到有人（不管判没判断）：用来判断离没离开座位。"""
        a, b = self.last_row_received_at, self.last_crowded_at
        if a is not None and b is not None:
            return swift_max(a, b)
        return a if a is not None else b

    # ---------- 输入 ----------

    def ingest(self, incoming, now):
        # 轻弯腰（slouch 但 eye_drop < 0.4，或者没有 eye_drop）：当成没有这个问题。
        row = incoming.copy()
        if Issue.SLOUCH in row.issues and (row.eye_drop if row.eye_drop is not None else 0) < self.settings.slouch_remind_eye_drop:
            row.issues = [i for i in row.issues if i != Issue.SLOUCH]
        self._last_time = now
        if self.paused:
            return
        if self.latest_row is not None and row.t < self.latest_row.t:
            return   # 时间倒退的行不收
        previous_seen = self._last_seen_at
        if not row.judged:
            # 画面里不止一个人：这一刻没在判断。不进窗口、不算坐正，也不能因此算“改过来了”。
            self.last_crowded_at = now
            self._ok_since = None
            return
        if self.latest_row is not None and row.t - self.latest_row.t > self.settings.sitting_reset_after:
            self._sitting_start = None
            self._sitting_armed = True
        if self._sitting_start is None:
            self._sitting_start = row.t
        self.latest_row = row
        self.last_row_received_at = now

        # 久坐提醒之后离开过座位，人回来了：这时候给奖励。
        if self._pending_sitting_reward and (previous_seen is None or now - previous_seen > self.settings.away_after):
            self._pending_sitting_reward = False
            self._give_reward(None, "camera_back_in_view", now)

        sides = {}
        for issue in row.issues:
            side = row.side(issue)
            if side is not None:
                sides[issue] = side
        self._history.append((row.t, frozenset(row.issues), sides))
        windows = [r.window for r in self.settings.rules.values()]
        horizon = swift_max(self.settings.upright_rule.window, max(windows) if windows else 0) + 10
        self._history = [h for h in self._history if not (row.t - h[0] > horizon)]

        # 提醒中：看着有没有改过来。
        current = self.reminder
        if current is not None and current.kind == ReminderKind.POSTURE and current.issue is not None:
            issue = current.issue
            if issue in row.issues:
                self._ok_since = None
            elif self._ok_since is None:
                self._ok_since = row.t
            if self._ok_since is not None and row.t - self._ok_since >= self.settings.corrected_after:
                self._record("pet_saw_corrected", {"issue": issue.value, "reminder": str(current.id),
                                                   "seconds": str(int(now - current.started_at))}, now)
                self._close_reminder()
                self._give_reward(issue, "log", now, since_reminder=now - current.started_at)

        # 每种姿势在自己的窗口里占到门槛提醒一次，降到 30% 以下（并且过了 20 分钟）才再提。
        for issue in ALL_ISSUES:
            rule = self.settings.rule(issue)
            share = self.share(issue, row.t)
            if share is None:
                continue
            if share < self.settings.rearm_share:
                self._armed[issue.value] = True
            if not (share >= rule.share and self._armed.get(issue.value, True)):
                continue
            side = None
            shown = share
            if rule.same_side:
                # 歪头、高低肩、扭头：要“往同一边”的行自己就占到门槛，左右来回换不算。
                result = self.same_side_share(issue, row.t)
                if result is None or not (result[1] >= rule.share):
                    continue
                side, shown = result
            if self._remind(ReminderKind.POSTURE, issue, side, shown, now):
                self._armed[issue.value] = False
        # 坐直也是一种姿势：一直不动太久，提醒换个坐姿。和别的姿势同时到门槛时，别的先提（上面先轮到）。
        share = self.upright_share(row.t)
        if share is not None:
            if share < self.settings.rearm_share:
                self._armed["upright"] = True
            if (share >= self.settings.upright_rule.share and self._armed.get("upright", True)
                    and self._remind(ReminderKind.UPRIGHT, None, None, share, now)):
                self._armed["upright"] = False

        start = self._sitting_start
        if (start is not None and self._sitting_armed and (row.t - start) / 60 >= self.settings.sitting_minutes
                and self._remind(ReminderKind.SITTING, None, None, None, now)):
            self._sitting_armed = False

    def ingest_alert(self, alert, now):
        """后端的提醒结果（log-alerts.csv）只记下来做对照，不触发任何东西。"""
        self._last_time = now
        if self.paused:
            return
        details = {"kind": alert.kind.value, "issue": alert.issue.value if alert.issue is not None else "",
                   "outcome": alert.outcome.value, "alert_time": fmt2(alert.alert_time)}
        if self.reminder is not None:
            details["pet_reminder"] = str(self.reminder.id)
        self._record("backend_alert_result", details, now)

    def tick(self, now):
        """每秒一次。"""
        self._last_time = now
        if self.paused:
            return
        if self.snooze_until is not None and now >= self.snooze_until:
            self.snooze_until = None
            self._record("snooze_finished", {}, now)
        if self.reward is not None and now - self.reward.started_at >= self.reward.seconds:
            self.reward = None
        current = self.reminder
        if current is None:
            return
        if current.kind == ReminderKind.UPRIGHT:
            # 换个坐姿可能就是出现别的姿势，不判断“改过来”，也不给爱心：说完安静收尾。
            if now - current.started_at >= self.settings.upright_bubble_seconds:
                self._record("reminder_closed_quietly", {"outcome": "shown", "issue": "upright",
                                                         "reminder": str(current.id)}, now)
                self._close_reminder()
            return
        follow = self.settings.posture_follow if current.kind == ReminderKind.POSTURE else self.settings.sitting_follow
        seen = self._last_seen_at
        unseen = now - seen if seen is not None else 0
        if current.kind == ReminderKind.SITTING and unseen >= self.settings.left_seat_after:
            self._record("camera_left_seat", {"source": "log", "reminder": str(current.id)}, now)
            self._close_reminder()
            self._wait_for_return(now)
        elif current.kind == ReminderKind.POSTURE and unseen >= self.settings.left_seat_after:
            self._record("reminder_closed_quietly", {"outcome": "left_seat", "reminder": str(current.id)}, now)
            self._close_reminder()
        elif now - current.started_at >= follow:
            self._record("reminder_timed_out", {"issue": current.issue.value if current.issue is not None else "sitting",
                                                "reminder": str(current.id)}, now)
            self._close_reminder()

    # ---------- 用户 ----------

    def snooze(self, now):
        """“稍后”：真的安静一段时间。当前的提醒作废，之后不补发。"""
        self._last_time = now
        if self.paused:
            return
        current = self.reminder
        if current is not None:
            self._record("user_snoozed", {"reminder": str(current.id),
                                          "issue": current.issue.value if current.issue is not None else "sitting"}, now)
            self._close_reminder()
        else:
            self._record("user_snoozed", {}, now)
        self._pending_sitting_reward = False
        self.reward = None
        self.snooze_until = now + self.settings.snooze_seconds

    def cancel_snooze(self, now):
        if self.snooze_until is None:
            return
        self.snooze_until = None
        self._record("snooze_cancelled", {}, now)

    def acknowledge(self, now):
        """“知道了”：收起气泡，继续等结果。"""
        current = self.reminder
        if current is None or current.bubble_hidden:
            return
        current = dataclasses.replace(current, bubble_hidden=True)
        self.reminder = current
        self._record("user_acknowledged", {"reminder": str(current.id)}, now)

    def set_paused(self, value, now):
        self._last_time = now
        if value == self.paused:
            return
        if value:
            if self.reminder is not None:
                self._record("reminder_closed_quietly", {"outcome": "paused", "reminder": str(self.reminder.id)}, now)
            self._close_reminder()
            self.reward = None
            self.snooze_until = None
            self._pending_sitting_reward = False
        self.paused = value
        # 恢复以后从头看，不拿暂停前的数据下判断。
        self._history = []
        self._armed = {}
        self.latest_row = None
        self.last_row_received_at = None
        self.last_crowded_at = None
        self._sitting_start = None
        self._sitting_armed = True
        self._ok_since = None
        self._record("paused" if value else "resumed", {}, now)

    def reward_presented(self, reward_id, played, now):
        """画面层播完（或播不了）奖励时告诉这里，照实记。"""
        self._record("reward_played" if played else "reward_unavailable", {"reward": str(reward_id)}, now)

    # ---------- 输出 ----------

    def scene(self, now):
        if self.paused:
            return Paused()
        if self.reward is not None:
            return Rewarding(self.reward)
        if self.snooze_until is not None and now < self.snooze_until:
            return Snoozed(self.snooze_until)
        crowded = self.last_crowded_at
        if (crowded is not None and now - crowded <= self.settings.away_after
                and crowded >= (self.last_row_received_at if self.last_row_received_at is not None else 0)):
            if self.reminder is not None:
                return Reminding(self.reminder)
            return Waiting(CROWDED_TEXT)
        received, row = self.last_row_received_at, self.latest_row
        if received is None or row is None:
            return Waiting(self.input_status)
        if now - received > self.settings.away_after:
            return Away()
        if self.reminder is not None:
            return Reminding(self.reminder)
        issue = self._primary_issue(row)
        if issue is not None:
            s = self.share(issue, row.t, require_full_window=False)
            share = s if s is not None else 0
            return Drifting(issue, row.side(issue), swift_min(1.0, 0.35 + 0.65 * share))
        return Calm()

    def share(self, issue, t, require_full_window=True):
        """0–1：过去这种姿势自己的窗口里，它占了多少行。窗口还没攒满就返回 None。"""
        return self._share(self.settings.rule(issue).window, t, require_full_window, lambda h: issue in h[1])

    @property
    def reminder_sees_correction(self):
        """提醒之后已经看到回正、但还没满 10 秒（只读，给画面用）。"""
        return self.reminder is not None and self.reminder.kind == ReminderKind.POSTURE and self._ok_since is not None

    def reminder_progress(self, issue, t):
        """这个姿势离提醒门槛走到哪了（只读，给画面用，不影响提醒的判断）。到门槛、窗口也攒满时 ≥ 1。"""
        rule = self.settings.rule(issue)
        rows = [h for h in self._history if t - h[0] <= rule.window]
        if not rows or not (rule.share > 0) or not (rule.window > 0):
            return 0
        share = sum(1 for h in rows if issue in h[1]) / len(rows)
        needed = rule.window * self.settings.window_coverage - self.settings.coverage_gap_cap
        filled = swift_min(1.0, self._coverage(rows) / needed) if needed > 0 else 1.0
        return share * filled / rule.share

    def upright_share(self, t, require_full_window=True):
        """0–1：“坐直”（判断过、没有任何问题）的行占多少。"""
        return self._share(self.settings.upright_rule.window, t, require_full_window, lambda h: not h[1])

    def _share(self, window, t, require_full_window, match):
        rows = [h for h in self._history if t - h[0] <= window]
        if not rows:
            return None
        if require_full_window and self._coverage(rows) < window * self.settings.window_coverage - self.settings.coverage_gap_cap:
            return None
        return sum(1 for h in rows if match(h)) / len(rows)

    def _coverage(self, rows):
        """这些行真正覆盖了多少秒：相邻两行之间的间隔累加，大空档每段最多算几秒。"""
        total = 0.0
        for i in range(1, max(1, len(rows))):
            total += swift_min(self.settings.coverage_gap_cap, swift_max(0.0, rows[i][0] - rows[i - 1][0]))
        return total

    def same_side_share(self, issue, t):
        """窗口里“这个姿势、而且往同一边”的行占多少：返回占得多的那一边和它的占比。"""
        rows = [h for h in self._history if t - h[0] <= self.settings.rule(issue).window]
        if not rows:
            return None
        left = sum(1 for h in rows if issue in h[1] and h[2].get(issue) == Side.LEFT)
        right = sum(1 for h in rows if issue in h[1] and h[2].get(issue) == Side.RIGHT)
        if not (left + right > 0):
            return None
        return (Side.LEFT, left / len(rows)) if left >= right else (Side.RIGHT, right / len(rows))

    def _primary_issue(self, row):
        """同时有几个问题时，演占比最高的那个（一样高取前面那个，和 Swift 的 max(by:) 一样）。"""
        best = None
        best_share = None
        for issue in row.issues:
            s = self.share(issue, row.t, require_full_window=False)
            s = s if s is not None else 0
            if best is None or best_share < s:
                best, best_share = issue, s
        return best

    # ---------- 内部 ----------

    def _remind(self, kind, issue, side, share, now):
        """返回 True：这次提醒用掉了（演出来了，或者按规矩吞掉了）。False：正忙着别的，下一行再试。"""
        key = issue.value if issue is not None else kind.value

        def swallow(reason):
            self._record("reminder_swallowed", {"issue": key, "reason": reason}, now)
            return True

        if self.snooze_until is not None and now < self.snooze_until:
            return swallow("snoozed")
        if self.reminder is not None or self.reward is not None or self._pending_sitting_reward:
            return False
        if self._last_reminder_closed_at is not None and now - self._last_reminder_closed_at < self.settings.quiet_after_reminder:
            return False
        if self._reminder_times and now - self._reminder_times[-1] < self.settings.min_gap_between_reminders:
            return False
        self._reminder_times = [x for x in self._reminder_times if not (now - x > 3600)]
        if len(self._reminder_times) >= self.settings.max_reminders_per_hour:
            return swallow("hourly_cap")
        last = self._last_reminder_at.get(key)
        if last is not None and now - last < self.settings.same_issue_gap:
            return swallow("same_issue_gap")
        self._reminder_times.append(now)
        self._last_reminder_at[key] = now
        self._ok_since = None
        if kind == ReminderKind.SITTING:
            line = Lines.sitting(int(swift_rounded(self.settings.sitting_minutes)))
        elif kind == ReminderKind.UPRIGHT:
            line = Lines.upright
        else:
            line = Lines.reminder(issue, side, self.settings.rule(issue).window)
        new = Reminder(id=self._next_id, kind=kind, issue=issue, side=side, line=line, started_at=now)
        self._next_id += 1
        self.reminder = new
        details = {"reminder": str(new.id), "kind": kind.value, "issue": key}
        if share is not None:
            details["share"] = fmt2(share)
        if side is not None:
            details["side"] = side.value
        self._record("pet_reminder", details, now)
        return True

    def _close_reminder(self):
        if self.reminder is None:
            return
        self._last_reminder_closed_at = self._last_time
        self.reminder = None
        self._ok_since = None

    def _wait_for_return(self, now):
        # 人还在画面外：等回来再给。已经回来了（后端的结果晚到）：现在给。
        unseen = now - self.last_row_received_at if self.last_row_received_at is not None else float("inf")
        if unseen <= self.settings.away_after:
            self._give_reward(None, "camera_back_in_view", now)
        else:
            self._pending_sitting_reward = True

    def _give_reward(self, issue, source, now, since_reminder=None):
        """since_reminder：提醒弹出到确认改过来过了多久（久坐回来没有）。"""
        today = 0 if issue is None else self._corrections.add(now)
        kind = self.settings.rewards.pick(issue, since_reminder, today)
        seconds = self.settings.rewards.seconds.get(kind)
        new = Reward(id=self._next_id, issue=issue, line=RewardRules.line(kind, issue, today), started_at=now,
                     kind=kind, today=today, seconds=seconds if seconds is not None else self.settings.reward_seconds)
        self._next_id += 1
        self.reward = new
        details = {"reward": str(new.id), "issue": issue.value if issue is not None else "sitting", "source": source,
                   "gif": kind.value}
        if issue is not None:
            details["today"] = str(today)
        self._record("reward_given", details, now)

    def _record(self, kind, details, now):
        if self.on_event is not None:
            self.on_event(kind, details, now)


class Lines:
    """桌宠说的话。现在只说“哪个姿势、多久了”，先不教具体动作。"""

    @staticmethod
    def duration(window):
        if window >= 60:
            return f"快 {int(swift_rounded(window / 60))} 分钟"
        return f"{int(window)} 秒"

    @staticmethod
    def side_name(side):
        return "右" if side == Side.RIGHT else "左"

    @staticmethod
    def reminder(issue, side, window):
        d = Lines.duration(window)
        if issue == Issue.HEAD_DOWN:
            return f"你低头{d}啦～"
        if issue == Issue.FORWARD_HEAD:
            return f"你的头往前探了{d}啦～"
        if issue == Issue.SHRUG:
            return f"你耸着肩{d}啦～"
        if issue == Issue.HEAD_TILT:
            return f"你的头往{Lines.side_name(side)}歪了{d}啦～"
        if issue == Issue.UNEVEN_SHOULDERS:
            return f"你的{Lines.side_name(side)}肩高了{d}啦～"
        if issue == Issue.HEAD_TURN:
            return f"你一直往{Lines.side_name(side)}边扭着头{d}啦～"
        if issue == Issue.SLOUCH:
            return f"你弯着腰{d}啦～"
        if issue == Issue.LEAN_BACK:
            return f"你往后靠了{d}啦～"
        return f"你快滑到椅子下面去啦，已经 {max(1, int(swift_rounded(window / 60)))} 分钟了～"

    @staticmethod
    def sitting(minutes):
        return f"你已经坐了 {minutes} 分钟啦～"

    # 坐直不动太久（gui 10-27 改的文案）。
    upright = "坐得很直很久啦，起来走两步、伸个懒腰吧"

    @staticmethod
    def reward(issue):
        return {
            Issue.HEAD_DOWN: "头抬起来啦！脖子在谢谢你～",
            Issue.FORWARD_HEAD: "头收回来啦，脖子松快多了～",
            Issue.SHRUG: "肩膀放下来啦，舒服～",
            Issue.HEAD_TILT: "头正回来啦～",
            Issue.UNEVEN_SHOULDERS: "两边肩膀一样高啦～",
            Issue.HEAD_TURN: "转回来啦，脖子松了～",
            Issue.SLOUCH: "背直起来啦～",
            Issue.LEAN_BACK: "坐回来啦～",
            Issue.SLIDE: "坐回来啦，腰舒服多了～",
        }[issue]

    sitting_reward = "回来啦！走一走，腰舒服多了吧～"
