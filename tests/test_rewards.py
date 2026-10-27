"""照翻 Pet2/Tests/RewardTests.swift：奖励放哪个 GIF，以及 Brain 里一次次改过来时轮到哪个。"""
import time

from pet2win.core.brain import Brain, Lines, ReminderKind, Reminding, Reward, Rewarding, Settings
from pet2win.core.posture_input import ALL_ISSUES, Issue, PostureRow
from pet2win.core.rewards import ALL_REWARD_KINDS, CorrectionCounter, RewardKind, RewardRules

from .support import Recorder, check, feed, is_reminding, is_rewarding


def _noon():
    """本地日期的中午：测试里来回几分钟都不会跨零点。和 Swift 一样是“当天零点 + 12 小时”。"""
    lt = time.localtime(1_793_000_000)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return midnight + 12 * 3600


def _row(brain, t, issues):
    """喂一行、走一下钟，返回这一刻的画面。歪头一律往左。"""
    brain.ingest(PostureRow.make(t, issues, tilt_to_left=12 if Issue.HEAD_TILT in issues else None), now=t)
    brain.tick(now=t)
    return brain.scene(t)


def _fix_once(brain, start, slow):
    """一轮：坐正 30 秒 → 歪头直到提醒（slow 就再歪 40 秒）→ 头回正直到给奖励。每 2 秒一行。"""
    t = start
    for _ in range(15):
        _row(brain, t, []); t += 2
    reminded_at = None
    while reminded_at is None and t < start + 120:
        if is_reminding(_row(brain, t, [Issue.HEAD_TILT])):
            reminded_at = t
        t += 2
    if slow:
        for _ in range(20):
            _row(brain, t, [Issue.HEAD_TILT]); t += 2
    reward = None
    while reward is None and t < start + 240:
        s = _row(brain, t, [])
        if isinstance(s, Rewarding):
            reward = s.reward
        t += 2
    return reward, reminded_at, t


def r1():
    # R1. 规则：从上往下，先对上的算
    rules = RewardRules()
    check(rules.pick(None, None, 0) == RewardKind.CHIN_REST, "久坐回来 → 托腮")
    check(rules.pick(Issue.HEAD_TILT, 12, 1) == RewardKind.HEART, "今天第 1 次：改得快也送爱心")
    check(rules.pick(Issue.HEAD_DOWN, 200, 1) == RewardKind.HEART, "今天第 1 次：改得慢也送爱心")
    check(rules.pick(Issue.HEAD_TILT, 25, 2) == RewardKind.THUMBS_UP, "提醒后 25 秒改过来 → 点赞")
    check(rules.pick(Issue.HEAD_TILT, 40, 3) == RewardKind.THUMBS_UP, "正好 40 秒还算快")
    check(rules.pick(Issue.HEAD_TILT, 41, 3) == RewardKind.PLUS1, "超过 40 秒 → +1")
    check(rules.pick(Issue.SHRUG, None, 4) == RewardKind.PLUS1, "不知道隔了多久 → +1")
    check(rules.pick(Issue.HEAD_TILT, 90, 5) == RewardKind.HEART and rules.pick(Issue.HEAD_TILT, 10, 10) == RewardKind.HEART,
          "第 5、10 次送爱心，快慢都是")
    check(rules.pick(Issue.HEAD_TILT, 10, 6) == RewardKind.THUMBS_UP, "第 6 次又按快慢")
    only_first = RewardRules()
    only_first.heart_every = 0
    check(only_first.pick(Issue.HEAD_TILT, 90, 5) == RewardKind.PLUS1
          and only_first.pick(Issue.HEAD_TILT, 90, 1) == RewardKind.HEART, "heartEvery = 0：只有第 1 次送爱心")
    check(RewardKind.THUMBS_UP.value == "thumbs_up" and RewardKind.CHIN_REST.value == "chin_rest", "名字和素材文件夹一样")

    check(RewardRules.line(RewardKind.CHIN_REST, None, 0) == Lines.sitting_reward, "托腮说久坐回来的话")
    check(RewardRules.line(RewardKind.HEART, Issue.HEAD_TILT, 1) == Lines.reward(Issue.HEAD_TILT), "第 1 次的爱心说原来那句")
    check(RewardRules.line(RewardKind.HEART, Issue.HEAD_TILT, 5) == "今天第 5 次改过来啦，送你一颗心～", "第 5 次的爱心说第几次")
    check(RewardRules.line(RewardKind.THUMBS_UP, Issue.HEAD_DOWN, 2) == "好快！" + Lines.reward(Issue.HEAD_DOWN), "点赞夸快")
    check(RewardRules.line(RewardKind.PLUS1, Issue.SHRUG, 3) == "+1！今天第 3 次改过来啦～", "+1 数次数")
    for kind in ALL_REWARD_KINDS:
        for issue in ALL_ISSUES:
            line = RewardRules.line(kind, issue, 15)
            check(len(line) <= 40, f"奖励的话不超过 40 个字：{line}")
    check(Reward(id=-1, issue=Issue.HEAD_DOWN, line="x", started_at=0).kind == RewardKind.HEART, "没说放哪个的奖励（引导里的演示）放爱心")

    noon = _noon()
    counter = CorrectionCounter()
    same_day = [counter.add(noon), counter.add(noon + 3600), counter.add(noon + 10 * 3600)]
    check(same_day == [1, 2, 3], f"同一天一直往上数：{same_day}")
    next_day = counter.add(noon + 14 * 3600)
    check(next_day == 1, f"过了零点从 1 开始：{next_day}")


def r2():
    # R2. 歪头一次次改过来：第 1 次爱心 → 快的点赞 → 慢的 +1 → 快的点赞 → 第 5 次爱心（慢也是）
    s = Settings.demo()
    s.sitting_minutes = 9999
    s.upright_rule.share = 2   # 坐直太久另有单测，这里不提
    brain = Brain(s)
    rec = Recorder(); rec.attach(brain)
    t = _noon()
    plan = [(False, RewardKind.HEART), (False, RewardKind.THUMBS_UP), (True, RewardKind.PLUS1),
            (False, RewardKind.THUMBS_UP), (True, RewardKind.HEART)]
    last = None
    for n, (slow, kind) in enumerate(plan):
        reward, reminded_at, t = _fix_once(brain, t, slow)
        if reward is None or reminded_at is None:
            check(False, f"第 {n + 1} 轮：应该提醒、改过来、给奖励")
            break
        took = int(reward.started_at - reminded_at)
        check(reward.kind == kind, f"今天第 {n + 1} 次、提醒后 {took} 秒改过来 → {kind.value}（实际 {reward.kind.value}）")
        check(reward.today == n + 1, f"今天第 {n + 1} 次（实际 {reward.today}）")
        check(reward.seconds == s.rewards.seconds[kind], "整段长度按这种奖励自己的")
        check(reward.line == RewardRules.line(kind, Issue.HEAD_TILT, n + 1), f"气泡的话跟着换：{reward.line}")
        last = reward
    given = [d for k, d in rec.events if k == "reward_given"]
    check([d.get("gif", "") for d in given] == ["heart", "thumbs_up", "plus1", "thumbs_up", "heart"], "事件里记了放哪个 GIF")
    check([d.get("today", "") for d in given] == ["1", "2", "3", "4", "5"], "事件里记了今天第几次")
    if last is not None:
        almost = last.started_at + last.seconds - 0.5
        brain.tick(now=almost)
        check(is_rewarding(brain.scene(almost)), "爱心还没演完")
        end = last.started_at + last.seconds
        brain.tick(now=end)
        check(not is_rewarding(brain.scene(end)), "按爱心自己的长度收")


def r3():
    # R3. 久坐提醒后离开、回来 → 托腮；不算进“今天改过来几次”
    s = Settings.demo()
    s.upright_rule.share = 2
    brain = Brain(s)
    rec = Recorder(); rec.attach(brain)
    t = feed(brain, _noon(), 122, [])
    sc = brain.scene(t - 2)
    if not (isinstance(sc, Reminding) and sc.reminder.kind == ReminderKind.SITTING):
        check(False, "坐满 2 分钟应该提醒起身")
        return
    t += 70; brain.tick(now=t)   # 人走开了
    t += 30; brain.tick(now=t)
    sc = _row(brain, t, [])
    if not isinstance(sc, Rewarding):
        check(False, "人回来应该给奖励")
        return
    reward = sc.reward
    check(reward.kind == RewardKind.CHIN_REST and reward.issue is None and reward.today == 0, "久坐回来 → 托腮，不算第几次")
    check(reward.line == Lines.sitting_reward, "托腮说久坐回来的话")
    event = next((d for k, d in reversed(rec.events) if k == "reward_given"), None)
    check(event is not None and event.get("gif") == "chin_rest" and "today" not in event, "事件里记了托腮，没有第几次")
    nxt, _, _ = _fix_once(brain, t + 2, False)
    check(nxt is not None and nxt.kind == RewardKind.HEART and nxt.today == 1, "之后第一次改姿势还是今天第 1 次：送爱心")


ALL = [r1, r2, r3]
