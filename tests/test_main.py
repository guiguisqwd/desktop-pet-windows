"""照翻 Pet2/Tests/main.swift：只测 core（只用标准库）。用几段假的 log 行，不要摄像头。
每一段的编号、顺序、检查的话都和 Swift 版一样，Mac 版加了测试就照着在这里加。"""
import os
import shutil
import tempfile
import uuid

from pet2win.core.backoff import Backoff, PostureExit
from pet2win.core.brain import (Away, Brain, CROWDED_TEXT, Drifting, Lines, Paused, ReminderKind, Reminding,
                                Rewarding, Rule, Settings, Snoozed, Waiting)
from pet2win.core.csv_tail import CSVTail, MISSING, WAITING_HEADER, reading
from pet2win.core.drowsiness import Drowsiness
from pet2win.core.posture_input import AlertKind, AlertOutcome, AlertRow, Issue, PostureRow, Side

from .support import (BASE as base, Recorder, check, fatal, feed, is_drifting, is_reminding, is_rewarding, pattern,
                      quiet_settings)


def t1():
    # 1. 出现低头 → 演下一步（不出声）→ 攒够占比 → 提醒 → 后端说改过来了 → 奖励 → 回到平常
    brain = Brain(Settings.demo())
    rec = Recorder(); rec.attach(brain)
    check(brain.scene(base) == Waiting("还没收到体态数据"), "没数据时是等待")
    t = feed(brain, base, 10, [])
    check(brain.scene(t) == Calm_(), "坐正时是平常")
    brain.ingest(PostureRow.make(t, [Issue.HEAD_DOWN]), now=t)
    check(is_drifting(brain.scene(t), Issue.HEAD_DOWN), "刚出现低头：演下一步，不提醒")
    check(rec.count("pet_reminder") == 0, "刚出现不提醒")
    t = feed(brain, t + 2, 20, [Issue.HEAD_DOWN])
    s = brain.scene(t)
    if not isinstance(s, Reminding):
        fatal("应该在提醒")
    reminder = s.reminder
    check(reminder.issue == Issue.HEAD_DOWN and "低头" in reminder.line, "提醒的是低头")
    check(len(reminder.line) <= 40, f"提醒不超过 40 个字：{len(reminder.line)}")
    check(rec.count("pet_reminder") == 1, "只提醒一次")
    t = feed(brain, t, 4, [Issue.HEAD_DOWN])
    check(rec.count("pet_reminder") == 1, "还低着头也不重复提醒")
    # 后端写来的结果只记录，不触发奖励
    brain.ingest_alert(AlertRow(alert_time=reminder.started_at + 1, kind=AlertKind.POSTURE, issue=Issue.HEAD_DOWN,
                                outcome=AlertOutcome.CORRECTED, outcome_time=t), now=t)
    check(is_reminding(brain.scene(t)), "log-alerts.csv 不触发任何东西")
    check(rec.count("backend_alert_result") == 1, "但单独记了一笔")
    # 桌宠自己看到：低头连续 10 秒没再出现
    t = feed(brain, t, 12, [])
    s = brain.scene(t)
    if not isinstance(s, Rewarding):
        fatal("应该在奖励")
    reward = s.reward
    check("抬起来" in reward.line, "奖励回应刚才那件事")
    check(rec.count("pet_saw_corrected") == 1, "记下：桌宠看到改过来")
    brain.reward_presented(reward.id, played=True, now=t)
    check(rec.count("reward_played") == 1, "记下：奖励播了")
    t = feed(brain, t, 10, [])
    check(brain.scene(t) == Calm_(), "奖励演完回到平常")
    check(rec.count("reward_given") == 1, "只奖励一次")


def Calm_():
    from pet2win.core.brain import Calm
    return Calm()


def t1b():
    # 1b. 没到门槛不提醒；中间偶尔正常一下不清零
    brain = Brain(Settings.demo())
    rec = Recorder(); rec.attach(brain)
    t = base
    # 低头和正常各半：占 50%，一直不到 70%
    for k in range(50):   # 100 秒，不到演示里 2 分钟的久坐提醒
        brain.ingest(PostureRow.make(t, [Issue.HEAD_DOWN] if k % 2 == 0 else []), now=t); brain.tick(now=t); t += 2
    check(rec.count("pet_reminder") == 0, "占 50% 不提醒")
    brain.ingest(PostureRow.make(t, [Issue.HEAD_DOWN]), now=t)
    check(is_drifting(brain.scene(t), Issue.HEAD_DOWN), "但出现时照样演下一步")
    check(rec.count("pet_reminder") == 0, "还是不提醒")

    brain2 = Brain(Settings.demo())
    rec2 = Recorder(); rec2.attach(brain2)
    t = base
    # 每 5 行里有 1 行正常（动一动、想事情）：占 80%
    for k in range(15):
        brain2.ingest(PostureRow.make(t, [] if k % 5 == 4 else [Issue.HEAD_DOWN]), now=t); brain2.tick(now=t); t += 2
    check(rec2.count("pet_reminder") == 1, "偶尔正常一下不清零，照样提醒")
    check(rec2.has("pet_reminder", "issue", "head_down"), "提醒的是对应的姿势")


def t1c():
    # 1c. 行频不影响：应用每秒一行、脚本 2.5 秒一行，判断一样（低头：5 分钟里占 80%）
    for step in [1.0, 2.5]:
        brain = Brain(quiet_settings())
        rec = Recorder(); rec.attach(brain)
        t = pattern(brain, base, 230, Issue.HEAD_DOWN, 0.85, step)
        check(rec.count("pet_reminder") == 0, f"{step} 秒一行：窗口没攒满（不到 240 秒），先不提醒")
        t = pattern(brain, t, 40, Issue.HEAD_DOWN, 0.85, step)
        check(rec.count("pet_reminder") == 1, f"{step} 秒一行：占 85% 提醒一次")
        share = brain.share(Issue.HEAD_DOWN, t - step) or 0
        check(abs(share - 0.85) < 0.03, f"{step} 秒一行：占比约 85%：{share}")


def t1e():
    # 1e. 每种姿势按自己的门槛：到了才提醒，没到不提醒
    cases = [
        # 姿势, 窗口, 门槛下, 门槛上, 方向值
        (Issue.FORWARD_HEAD, 300, 0.62, 0.76, None),
        (Issue.HEAD_DOWN, 300, 0.74, 0.86, None),
        (Issue.SHRUG, 180, 0.62, 0.76, None),
        (Issue.HEAD_TILT, 300, 0.62, 0.76, 12),
        (Issue.UNEVEN_SHOULDERS, 600, 0.74, 0.86, -4),
        (Issue.HEAD_TURN, 600, 0.74, 0.86, 0.3),
        # 2026-10-27.2 新加的三种（草稿门槛）
        (Issue.SLOUCH, 300, 0.62, 0.76, None),
        (Issue.SLIDE, 300, 0.62, 0.76, None),
        (Issue.LEAN_BACK, 600, 0.74, 0.86, None),
    ]
    for issue, window, below, above, side_value in cases:
        for fraction, expect in [(below, 0), (above, 1)]:
            brain = Brain(quiet_settings())
            rec = Recorder(); rec.attach(brain)
            pattern(brain, base, window + 30, issue, fraction, 1, side_value=side_value)
            check(rec.count("pet_reminder") == expect,
                  f"{issue.cn}：{int(window / 60)} 分钟里占 {int(fraction * 100)}% → {'提醒' if expect == 1 else '不提醒'}"
                  f"（实际 {rec.count('pet_reminder')}）")
            if expect == 1 and side_value is not None:
                side = next((d.get("side") for k, d in rec.events if k == "pet_reminder"), None)
                check(side == ("left" if side_value > 0 else "right"), f"{issue.cn}：提醒带方向 {side or '无'}")
    # 歪头左右来回换：占比够，但方向不一致，不提醒
    brain = Brain(quiet_settings())
    rec = Recorder(); rec.attach(brain)
    t = base
    for k in range(330):
        brain.ingest(PostureRow.make(t, [Issue.HEAD_TILT], tilt_to_left=12 if k % 2 == 0 else -12), now=t)
        brain.tick(now=t); t += 1
    check(rec.count("pet_reminder") == 0, "歪头左右各一半：方向不一致，不提醒")
    # 窗口没攒满：中间大段没看到人，判断过的行覆盖不到 80%
    gap = Brain(quiet_settings())
    rec_gap = Recorder(); rec_gap.attach(gap)
    t = pattern(gap, base, 100, Issue.FORWARD_HEAD, 1, 1)
    t += 150   # 没看到人
    pattern(gap, t, 60, Issue.FORWARD_HEAD, 1, 1)
    check(rec_gap.count("pet_reminder") == 0, "5 分钟窗口里只判断了 160 秒：不提醒")


def t1g():
    # 1g. 新的三种：解析、文案、改过来给爱心
    row = PostureRow.from_csv({"time": "1", "issues": "slouch|lean_back|slide", "severity": "0.8", "eye_drop": "0.5",
                               "head_size": "0.8"})
    check(row is not None and row.issues == [Issue.SLOUCH, Issue.LEAN_BACK, Issue.SLIDE], "解析弯腰、往后靠、滑坐")
    s = Settings()
    check(Lines.reminder(Issue.SLOUCH, None, s.rule(Issue.SLOUCH).window) == "你弯着腰快 5 分钟啦～", "弯腰的话")
    check(Lines.reminder(Issue.LEAN_BACK, None, s.rule(Issue.LEAN_BACK).window) == "你往后靠了快 10 分钟啦～", "往后靠的话")
    check(Lines.reminder(Issue.SLIDE, None, s.rule(Issue.SLIDE).window) == "你快滑到椅子下面去啦，已经 5 分钟了～", "滑坐的话")
    brain = Brain(quiet_settings())
    rec = Recorder(); rec.attach(brain)
    t = pattern(brain, base, 330, Issue.SLIDE, 1, 1)
    check(rec.has("pet_reminder", "issue", "slide"), "滑坐提醒了")
    t = pattern(brain, t, 12, Issue.SLIDE, 0, 1)
    check(rec.has("pet_saw_corrected", "issue", "slide") and rec.count("reward_given") == 1, "滑坐改过来照常给爱心")


def t1h():
    # 1h. 弯腰分轻重（2026-10-27.5）
    def slouch_row(t, eye_drop, extra=()):
        r = PostureRow.make(t, [Issue.SLOUCH] + list(extra))
        r.eye_drop = eye_drop
        return r

    # 轻弯腰（eye_drop 0.2）一直持续：不提醒弯腰，而且算坐直
    s = quiet_settings()
    s.upright_rule = Rule(300, 0.8)
    light = Brain(s)
    rec_l = Recorder(); rec_l.attach(light)
    t = base
    while t < base + 330:
        light.ingest(slouch_row(t, 0.2), now=t); light.tick(now=t); t += 1
    check(not rec_l.has("pet_reminder", "issue", "slouch"), "轻弯腰（eye_drop < 0.4）不提醒")
    check(rec_l.has("pet_reminder", "issue", "upright"), "轻弯腰的行算坐直")
    check(light.scene(t - 1) != Drifting(Issue.SLOUCH, None, 1), "轻弯腰不演弯腰")
    # 没有 eye_drop 的 slouch 行（旧日志）：也当轻弯腰
    old = Brain(quiet_settings())
    rec_o = Recorder(); rec_o.attach(old)
    t = base
    while t < base + 330:
        old.ingest(slouch_row(t, None), now=t); old.tick(now=t); t += 1
    check(rec_o.count("pet_reminder") == 0, "没有 eye_drop 的弯腰不提醒")
    # eye_drop ≥ 0.4：5 分钟里占 70% 提醒
    real = Brain(quiet_settings())
    rec_r = Recorder(); rec_r.attach(real)
    t = base
    while t < base + 330:
        real.ingest(slouch_row(t, 0.5), now=t); real.tick(now=t); t += 1
    check(rec_r.has("pet_reminder", "issue", "slouch"), "eye_drop ≥ 0.4 的弯腰才提醒")
    # 改过来：还报 slouch，但 eye_drop 降到 0.4 以下连续 10 秒，就算改过来
    t2 = t
    while t2 < t + 6:
        real.ingest(slouch_row(t2, 0.3), now=t2); real.tick(now=t2); t2 += 1
    check(rec_r.count("reward_given") == 0, "才 6 秒，还不算")
    while t2 < t + 13:
        real.ingest(slouch_row(t2, 0.3), now=t2); real.tick(now=t2); t2 += 1
    check(rec_r.has("pet_saw_corrected", "issue", "slouch") and rec_r.count("reward_given") == 1, "弯腰改过来按 eye_drop 判")
    # slouch 和 head_down 同一行：各算各的
    both = Brain(quiet_settings())
    t = base
    while t < base + 300:
        both.ingest(slouch_row(t, 0.5, extra=[Issue.HEAD_DOWN]), now=t); both.tick(now=t); t += 1
    check(abs((both.share(Issue.SLOUCH, t - 1) or 0) - 1) < 0.01 and abs((both.share(Issue.HEAD_DOWN, t - 1) or 0) - 1) < 0.01,
          "弯腰和低头同一行都算：各自占 100%")
    mixed = Brain(quiet_settings())
    t = base
    k = 0
    while t < base + 300:
        # 一半行 eye_drop 0.5（弯腰+低头），一半 0.2（轻弯腰+低头）
        mixed.ingest(slouch_row(t, 0.5 if k % 2 == 0 else 0.2, extra=[Issue.HEAD_DOWN]), now=t); mixed.tick(now=t)
        t += 1; k += 1
    check(abs((mixed.share(Issue.SLOUCH, t - 1) or 0) - 0.5) < 0.02, "轻弯腰的行不算进弯腰占比")
    check(abs((mixed.share(Issue.HEAD_DOWN, t - 1) or 0) - 1) < 0.01, "但低头照算")


def t1i():
    # 1i. 后台起不来时的退避和判断（2026-10-27.6）
    b = Backoff()
    delays = [b.next() for _ in range(8)]
    check(delays == [30, 60, 120, 240, 480, 600, 600, 600], f"退避 30 秒 → 1 → 2 → 4 → 8 → 最多 10 分钟：{delays}")
    b.reset()
    check(b.next() == 30, "点菜单重置后从 30 秒开始")
    check(PostureExit.is_permission_problem("OpenCV: not authorized to capture video (status 0), requesting..."), "没权限的那句认得出")
    check(PostureExit.is_permission_problem("10-27 00:01:02 等你允许使用摄像头……"), "中文的等权限也认得出")
    check(not PostureExit.is_permission_problem("OpenCV: camera failed to properly initialize!"), "只是打不开（被占着）不算没权限")
    check(not PostureExit.someone_else_running("", lambda _: True), ".running.pid 空的：没人在跑")
    check(not PostureExit.someone_else_running(None, lambda _: True), "没有这个文件：没人在跑")
    check(PostureExit.someone_else_running("12345\n", lambda p: p == 12345), "有进程号而且活着：有人在跑")
    check(not PostureExit.someone_else_running("12345", lambda _: False), "进程号对应的进程不在了：没人在跑")


def t1j():
    # 1j. 弯腰和头前伸同一行（2026-10-27.6 起会同时报）：各算各的
    brain = Brain(quiet_settings())
    rec = Recorder(); rec.attach(brain)
    t = base
    k = 0
    while t < base + 330:
        r = PostureRow.make(t, [Issue.FORWARD_HEAD] if k % 4 == 3 else [Issue.SLOUCH, Issue.FORWARD_HEAD])
        r.eye_drop = 0.5
        brain.ingest(r, now=t); brain.tick(now=t); t += 1; k += 1
    check(abs((brain.share(Issue.SLOUCH, t - 1) or 0) - 0.75) < 0.02, "弯腰占 75%")
    check(abs((brain.share(Issue.FORWARD_HEAD, t - 1) or 0) - 1) < 0.01, "头前伸占 100%")
    check(rec.count("pet_reminder") == 1, "同时到门槛也只提一次（每次提醒间隔 10 分钟）")


def t1k():
    # 1k. 犯困 / 睡着跟着“离提醒门槛多远”走（只读，不影响提醒）
    check(Drowsiness.stage(0.3) == Drowsiness.AWAKE, "不到门槛 40%：正常坐着")
    check(Drowsiness.stage(0.55) == Drowsiness.DROWSY, "40%～70%：犯困")
    check(Drowsiness.stage(0.75) == Drowsiness.ASLEEP, "70% 起：睡着")
    brain = Brain(quiet_settings())
    rec = Recorder(); rec.attach(brain)
    t = base
    seen = []
    progress_at_reminder = None
    while t < base + 330:
        brain.ingest(PostureRow.make(t, [Issue.HEAD_DOWN]), now=t); brain.tick(now=t)
        p = brain.reminder_progress(Issue.HEAD_DOWN, t)
        stage = Drowsiness.stage(p)
        if not seen or seen[-1] != stage:
            seen.append(stage)
        if progress_at_reminder is None and rec.count("pet_reminder") == 1:
            progress_at_reminder = p
        t += 1
    check(seen[:3] == [Drowsiness.AWAKE, Drowsiness.DROWSY, Drowsiness.ASLEEP], f"低头一直持续：先正常、再犯困、再睡着：{seen}")
    check((progress_at_reminder or 0) >= 1, f"提醒那一刻正好到门槛（progress ≥ 1）：{progress_at_reminder}")
    # 只是偶尔低头（占 30%）：一直不犯困
    light = Brain(quiet_settings())
    t = pattern(light, base, 330, Issue.HEAD_DOWN, 0.3, 1)
    check(Drowsiness.stage(light.reminder_progress(Issue.HEAD_DOWN, t - 1)) == Drowsiness.AWAKE, "偶尔低头不犯困")


def t1l():
    # 1l. 提醒后看到回正：先“注意到了”（只读），满 10 秒才算改正
    brain = Brain(quiet_settings())
    rec = Recorder(); rec.attach(brain)
    t = pattern(brain, base, 330, Issue.HEAD_DOWN, 1, 1)
    check(is_reminding(brain.scene(t)) and not brain.reminder_sees_correction, "提醒中，还没回正")
    t = pattern(brain, t, 3, Issue.HEAD_DOWN, 0, 1)
    check(brain.reminder_sees_correction and is_reminding(brain.scene(t)), "回正 3 秒：注意到了，但还在提醒、没算改正")
    check(rec.count("pet_saw_corrected") == 0 and rec.count("reward_given") == 0, "不记改正、不给爱心")
    t = pattern(brain, t, 2, Issue.HEAD_DOWN, 1, 1)
    check(not brain.reminder_sees_correction, "又低头了：回到提醒")
    t = pattern(brain, t, 11, Issue.HEAD_DOWN, 0, 1)
    check(rec.count("reward_given") == 1 and not brain.reminder_sees_correction, "连续 10 秒回正：算改正、给爱心")


def t1f():
    # 1f. 安静规则：两次提醒至少隔 10 分钟；同一姿势 20 分钟内不再提；坐直太久也提醒
    settings = Settings()
    settings.sitting_minutes = 9999
    settings.upright_rule.share = 2
    settings.quiet_after_reminder = 0
    settings.posture_follow = 30
    brain = Brain(settings)
    rec = Recorder(); rec.attach(brain)
    t = pattern(brain, base, 200, Issue.SHRUG, 1, 1)
    check(rec.has("pet_reminder", "issue", "shrug"), "耸肩提醒了")
    first = t
    # 马上又头前伸满 5 分钟：离上一次不到 10 分钟，先不提
    t = pattern(brain, t, 320, Issue.FORWARD_HEAD, 1, 1)
    check(rec.count("pet_reminder") == 1, "两次提醒之间不到 10 分钟：不提")
    t = pattern(brain, t, 600 - (t - first) + 5, Issue.FORWARD_HEAD, 1, 1)
    check(rec.has("pet_reminder", "issue", "forward_head"), "过了 10 分钟：头前伸提醒")
    # 耸肩又来：降到 30% 以下过了，但离上次耸肩提醒不到 20 分钟
    pattern(brain, t, 200, Issue.SHRUG, 1, 1)
    check(sum(1 for k, d in rec.events if d.get("issue") == "shrug" and k == "pet_reminder") == 1, "同一姿势 20 分钟内不再提")

    # 坐直：20 分钟里占 80% 提醒“换个坐姿”（窗口攒满 80% 即 16 分钟起开始算），不给爱心，说完安静收尾
    s2 = Settings()
    s2.sitting_minutes = 9999
    upright = Brain(s2)
    rec_u = Recorder(); rec_u.attach(upright)
    t = pattern(upright, base, 900, Issue.HEAD_DOWN, 0, 2)
    check(rec_u.count("pet_reminder") == 0, "坐直 15 分钟：窗口没攒满，不提醒")
    t = pattern(upright, t, 70, Issue.HEAD_DOWN, 0, 2)
    s = upright.scene(t)
    if not isinstance(s, Reminding):
        fatal("坐直太久应该提醒")
    r = s.reminder
    check(r.kind == ReminderKind.UPRIGHT and r.line == "坐得很直很久啦，起来走两步、伸个懒腰吧", "坐直的提醒用 gui 的原话")
    check(rec_u.has("pet_reminder", "issue", "upright"), "事件记成 upright")
    t = pattern(upright, t, 40, Issue.HEAD_DOWN, 0, 2)
    check(rec_u.count("reward_given") == 0 and rec_u.has("reminder_closed_quietly", "issue", "upright"),
          "坐直提醒不给爱心，说完安静收尾")

    # 同时到门槛：先提别的姿势（两个窗口一样长，同一行一起攒满）
    s3 = quiet_settings()
    s3.upright_rule = Rule(180, 0.25)
    both = Brain(s3)
    rec_b = Recorder(); rec_b.attach(both)
    pattern(both, base, 200, Issue.SHRUG, 0.72, 1)
    first_issue = next((d.get("issue") for k, d in rec_b.events if k == "pet_reminder"), None)
    check(first_issue == "shrug", "坐直和耸肩同时到：先提耸肩")


def t1d():
    # 1d. 画面里不止一个人：这一刻没在判断
    # 解析：persons=2、其他列空 → 没判断；旧日志没有 persons 列 → 照常
    crowd_row = PostureRow.from_csv({"time": "100", "issues": "", "severity": "", "persons": "2"})
    check(crowd_row is not None and crowd_row.judged is False, "多人行：没判断")
    one = PostureRow.from_csv({"time": "100", "issues": "", "severity": "0.00", "persons": "1"})
    check(one is not None and one.judged is True, "一个人：照常判断")
    old = PostureRow.from_csv({"time": "100", "issues": "head_down", "severity": "0.7"})
    check(old is not None and old.judged is True, "旧日志没有 persons 列：照常")

    def crowd(brain, t0, seconds):
        t = t0
        while t < t0 + seconds:
            brain.ingest(PostureRow.make(t, [], severity=0, persons=2, judged=False), now=t); brain.tick(now=t); t += 1
        return t

    # 只有多人行：不提醒，不算坐正，脚下说明
    a = Brain(Settings.demo())
    rec_a = Recorder(); rec_a.attach(a)
    t = crowd(a, base, 60)
    check(rec_a.count("pet_reminder") == 0, "多人行不提醒")
    check(a.scene(t) == Waiting(CROWDED_TEXT), "脚下说明画面里不止一个人")
    check(a.share(Issue.HEAD_DOWN, t, require_full_window=False) is None, "多人行不进窗口")

    # 低头里夹着多人行：窗口只算真正判断过的行
    b = Brain(Settings.demo())
    rec_b = Recorder(); rec_b.attach(b)
    t = base
    for k in range(40):
        row = PostureRow.make(t, [Issue.HEAD_DOWN]) if k % 2 == 0 else PostureRow.make(t, [], severity=0, persons=2, judged=False)
        b.ingest(row, now=t); b.tick(now=t); t += 1
    check(rec_b.count("pet_reminder") == 1, "夹着多人行，判断过的行全是低头：照样提醒")
    # 提醒后全是多人行：不能算改过来
    t = crowd(b, t, 30)
    check(rec_b.count("reward_given") == 0, "多人行不能算改过来")
    check(is_reminding(b.scene(t)), "提醒还在")
    t = feed(b, t, 12, [])
    check(rec_b.count("reward_given") == 1, "回到一个人、真的坐正 10 秒才给奖励")

    # 久坐提醒期间来了别人：不算离开座位
    c = Brain(Settings.demo())
    rec_c = Recorder(); rec_c.attach(c)
    t = feed(c, base, 122, [])
    check(rec_c.has("pet_reminder", "kind", "sitting"), "久坐提醒了")
    t = crowd(c, t, 90)
    check(rec_c.count("camera_left_seat") == 0, "多人行期间不算离开座位")
    check(rec_c.count("reward_given") == 0, "也不给奖励")


def t2():
    # 2. 改过来要连续 10 秒：8 秒还不算
    brain = Brain(Settings.demo())
    rec = Recorder(); rec.attach(brain)
    t = feed(brain, base, 26, [Issue.HEAD_DOWN])
    check(is_reminding(brain.scene(t)), "低头够久了在提醒")
    t = feed(brain, t, 8, [])
    check(is_reminding(brain.scene(t)), "才 8 秒，还在等")
    t = feed(brain, t, 4, [])
    check(is_rewarding(brain.scene(t)), "10 秒没低头：奖励")
    check(rec.count("pet_saw_corrected") == 1, "记下：桌宠看到改过来")
    # 后端的结果晚到：只记一笔，不再奖励一次
    brain.ingest_alert(AlertRow(alert_time=base + 20, kind=AlertKind.POSTURE, issue=Issue.HEAD_DOWN,
                                outcome=AlertOutcome.CORRECTED, outcome_time=t), now=t)
    check(rec.count("reward_given") == 1, "后端结果晚到不重复奖励")
    check(rec.count("backend_alert_result") == 1, "后端结果单独记一笔")


def t3():
    # 3. 稍后：当前提醒作废，期间的提醒全部吞掉，之后不补发
    brain = Brain(Settings.demo())
    rec = Recorder(); rec.attach(brain)
    t = feed(brain, base, 26, [Issue.HEAD_DOWN])
    check(is_reminding(brain.scene(t)), "在提醒")
    brain.snooze(now=t)
    check(rec.count("user_snoozed") == 1, "记下：用户点了稍后")
    check(isinstance(brain.scene(t), Snoozed), "稍后期间演安静")
    # 改过来再犯，期间本该提醒一次
    t = feed(brain, t, 30, [])
    t = feed(brain, t, 30, [Issue.HEAD_DOWN])
    check(isinstance(brain.scene(t), Snoozed), "还在稍后里")
    check(rec.has("reminder_swallowed", "reason", "snoozed"), "稍后期间的提醒被吞掉并记下")
    check(rec.count("reward_given") == 0, "稍后期间不奖励")
    # 稍后结束（10 分钟），人还低着头：不补发
    t = feed(brain, t, 600, [Issue.HEAD_DOWN], step=5)
    check(not is_reminding(brain.scene(t)), "稍后结束后不补发旧提醒")
    check(is_drifting(brain.scene(t), Issue.HEAD_DOWN), "只演下一步")
    check(rec.count("pet_reminder") == 1, "一共只提醒过一次")


def t4():
    # 4. 后端说没改过来：只记录；桌宠自己跟满 5 分钟没改过来才安静收尾
    brain = Brain(Settings.demo())
    rec = Recorder(); rec.attach(brain)
    t = feed(brain, base, 26, [Issue.HEAD_DOWN])
    s = brain.scene(t)
    if not isinstance(s, Reminding):
        fatal("应该在提醒")
    reminder = s.reminder
    t = feed(brain, t, 6, [Issue.HEAD_DOWN])
    brain.ingest_alert(AlertRow(alert_time=reminder.started_at, kind=AlertKind.POSTURE, issue=Issue.HEAD_DOWN,
                                outcome=AlertOutcome.NOT_CORRECTED, outcome_time=t), now=t)
    check(is_reminding(brain.scene(t)), "后端的结果不影响桌宠")
    check(rec.count("reward_given") == 0, "没奖励")


def t5():
    # 5. 5 分钟没改过来：安静收尾，不升级
    brain = Brain(Settings.demo())
    rec = Recorder(); rec.attach(brain)
    t = feed(brain, base, 26, [Issue.HEAD_DOWN])
    t = feed(brain, t, 250, [Issue.HEAD_DOWN])
    check(rec.count("reminder_timed_out") == 0, "4 分多钟还在等")
    t = feed(brain, t, 60, [Issue.HEAD_DOWN])
    check(rec.count("reminder_timed_out") == 1, "5 分钟没改过来：安静收尾")
    check(rec.count("reward_given") == 0, "不责备也不奖励")
    # （演示参数下坐满 2 分钟会提醒起身，那是另一件事）
    check(sum(1 for k, d in rec.events if k == "pet_reminder" and d.get("kind") == "posture") == 1, "姿势不再提醒")


def t6():
    # 6. 久坐：坐满 → 提醒 → 离开座位 → 回到画面里才奖励
    brain = Brain(Settings.demo())
    rec = Recorder(); rec.attach(brain)
    t = feed(brain, base, 122, [])
    s = brain.scene(t)
    if not isinstance(s, Reminding):
        fatal("应该提醒起身")
    reminder = s.reminder
    check(reminder.kind == ReminderKind.SITTING and "2 分钟" in reminder.line, "提醒久坐，带时长")
    # 人走了：没有新行
    t += 30; brain.tick(now=t)
    check(brain.scene(t) == Away(), "看不到人")
    t += 40; brain.tick(now=t)
    check(rec.has("camera_left_seat", "source", "log"), "离开 60 秒算起来了")
    check(rec.count("reward_given") == 0, "人不在画面里，先不放奖励")
    # 后端也写来“起来了”
    brain.ingest_alert(AlertRow(alert_time=reminder.started_at, kind=AlertKind.SITTING, issue=None,
                                outcome=AlertOutcome.STOOD_UP, outcome_time=t - 40), now=t)
    check(rec.count("reward_given") == 0, "后端确认也要等人回来")
    t += 60; brain.tick(now=t)
    brain.ingest(PostureRow.make(t, []), now=t)
    s = brain.scene(t)
    if not isinstance(s, Rewarding):
        fatal("人回来了应该奖励")
    check("回来" in s.reward.line, "久坐奖励的话")
    check(rec.count("reward_given") == 1, "只奖励一次")


def t6b():
    # 6b. 一次提醒结束后先安静一会儿，不接着催下一件事
    brain = Brain(Settings.demo())
    rec = Recorder(); rec.attach(brain)
    t = feed(brain, base, 26, [Issue.HEAD_DOWN])
    check(rec.count("pet_reminder") == 1, "低头提醒了")
    t = feed(brain, t, 296, [Issue.HEAD_DOWN])   # 跟满 5 分钟没改，安静收尾；这时早就坐满 2 分钟了
    check(rec.count("reminder_timed_out") == 1, "低头提醒收尾")
    t = feed(brain, t, 20, [Issue.HEAD_DOWN])
    check(rec.count("pet_reminder") == 1, "收尾后 30 秒内不提久坐")
    t = feed(brain, t, 16, [Issue.HEAD_DOWN])
    check(rec.has("pet_reminder", "kind", "sitting"), "安静过后再提久坐")


def t7():
    # 7. 歪头的方向写进提醒；暂停时什么都不收
    brain = Brain(Settings.demo())
    rec = Recorder(); rec.attach(brain)
    t = feed(brain, base, 26, [Issue.HEAD_TILT], tilt=18)
    s = brain.scene(t)
    if not isinstance(s, Reminding):
        fatal("应该提醒歪头")
    reminder = s.reminder
    check(reminder.side == Side.LEFT and "往左歪" in reminder.line, f"方向用本人的左右：{reminder.line}")
    brain.set_paused(True, now=t)
    check(brain.scene(t) == Paused(), "暂停")
    feed(brain, t, 60, [Issue.HEAD_DOWN])
    check(rec.count("pet_reminder") == 1, "暂停时不提醒")
    brain.set_paused(False, now=t + 60)
    check(brain.scene(t + 60) == Waiting("还没收到体态数据"), "恢复后从头等数据")


def t8():
    # 8. 每小时最多 2 次（正式参数）
    s = Settings()
    s.set_all_windows(20, upright=99999); s.same_issue_gap = 0; s.posture_follow = 1; s.quiet_after_reminder = 0
    s.min_gap_between_reminders = 0
    brain = Brain(s)
    rec = Recorder(); rec.attach(brain)
    t = base
    for _ in range(5):
        t = feed(brain, t, 26, [Issue.HEAD_DOWN])
        t = feed(brain, t, 26, [])
    check(rec.count("pet_reminder") == 2, f"所有姿势加起来一小时最多 2 次：{rec.count('pet_reminder')}")
    check(rec.has("reminder_swallowed", "reason", "hourly_cap"), "超出的记为吞掉")


def _write(path, text):
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def _append(path, text):
    with open(path, "ab") as f:
        f.write(text.encode("utf-8"))


def t9():
    # 9. 读文件：表头按列名、跳过旧行、追加、半行、清空、换文件、晚来的表头
    d = os.path.join(tempfile.gettempdir(), f"pet2-tests-{uuid.uuid4()}")
    os.makedirs(d)
    try:
        path = os.path.join(d, "log.csv")
        tail = CSVTail(path)
        check(tail.poll() == [] and tail.status == MISSING, "文件不在")
        # 启动后才出现的文件：里面都是新行
        _write(path, "time,label,issues,severity\n1,normal,head_down,0.7\n")
        rows = tail.poll()
        check(len(rows) == 1 and rows[0].get("issues") == "head_down", "后出现的文件从头读")
        _append(path, "2,normal,,0.0\n3,normal,shr")
        rows = tail.poll()
        check(len(rows) == 1 and rows[0]["time"] == "2", "只给写完的行")
        _append(path, "ug,0.6\n")
        rows = tail.poll()
        check(len(rows) == 1 and rows[0]["issues"] == "shrug", "半行补全后读出")
        r = PostureRow.from_csv(rows[0])
        check(r is not None and r.issues == [Issue.SHRUG], "解析成姿势")

        # 一启动就已经有旧行：跳过
        tail2 = CSVTail(path)
        check(tail2.poll() == [], "启动时已有的旧行跳过")
        check(tail2.status == reading(0), "但表头读到了")
        _append(path, "4,normal,head_down,0.8\n")
        check(len(tail2.poll()) == 1, "之后的新行照读")

        # 换成新文件（后端改名留旧日志、新建一个），列的顺序也变了
        os.remove(path)
        _write(path, "")
        check(tail2.poll() == [] and tail2.status == WAITING_HEADER, "新文件还没表头：等")
        _append(path, "time,issues,label,severity,tilt_to_left_deg\n5,head_tilt,normal,0.7,-12.5\n")
        rows = tail2.poll()
        check(len(rows) == 1, "表头来了，接着读")
        row = PostureRow.from_csv(rows[0]) if rows else None
        check(row is not None and row.issues == [Issue.HEAD_TILT] and row.side(Issue.HEAD_TILT) == Side.RIGHT, "按列名读，方向是右")

        # 被清空
        _write(path, "time,issues\n")
        tail2.poll()
        _append(path, "6,head_down\n")
        check(len(tail2.poll()) == 1, "清空后重新读")

        # 新版体态测试的做法：表头不一样就把旧文件改名成 log-old-日期.csv，再新建一个，多两列原始值
        old_header = "time,label,issues,severity,head_growth,tilt_to_left_deg,infer_ms,algo_version,source\n"
        _write(path, old_header + "10,normal,head_down,0.7,0.01,1.0,30,v1,mac\n")
        tail2.poll()
        _append(path, "11,normal,head_down,0.7,0.01,1.0,30,v1,mac\n")
        check(len(tail2.poll()) == 1, "旧表头的文件照读")
        os.replace(path, os.path.join(d, "log-old-20261026.csv"))
        tail2.poll()
        _write(path, "time,label,issues,severity,head_growth,tilt_to_left_deg,eye_y_px,ear_px,infer_ms,algo_version,source\n"
                     "12,normal,head_tilt,0.8,0.02,-15.0,312.5,88.1,31,v2,mac\n")
        rows = tail2.poll()
        renamed = PostureRow.from_csv(rows[0]) if rows else None
        check(len(rows) == 1 and renamed is not None and renamed.issues == [Issue.HEAD_TILT]
              and renamed.side(Issue.HEAD_TILT) == Side.RIGHT, "旧文件被改名、新建了多两列的新文件：从头读，按列名取值")

        # 结果文件
        alerts_path = os.path.join(d, "log-alerts.csv")
        _write(alerts_path, "time,kind,issue,direction,share,window_s,sit_min,channel,outcome,outcome_time,"
                            "seconds_to_outcome,algo_version,source\n")
        alerts = CSVTail(alerts_path)
        check(alerts.poll() == [], "结果文件只有表头")
        _append(alerts_path, "100.00,posture,head_down,,0.74,180,,notify,corrected,123.00,23.0,2026.10.26+3dff6d30,"
                             "mac:yolo11n-pose.pt\n")
        alert_rows = alerts.poll()
        alert = AlertRow.from_csv(alert_rows[0]) if alert_rows else None
        check(alert is not None and alert.outcome == AlertOutcome.CORRECTED and alert.issue == Issue.HEAD_DOWN
              and alert.outcome_time == 123, "解析提醒结果")
        check(CSVTail.split('a,"b,c",d') == ["a", "b,c", "d"], "带引号的字段")
    finally:
        shutil.rmtree(d, ignore_errors=True)


ALL = [t1, t1b, t1c, t1e, t1g, t1h, t1i, t1j, t1k, t1l, t1f, t1d, t2, t3, t4, t5, t6, t6b, t7, t8, t9]
