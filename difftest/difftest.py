"""和 Mac 版对拍：同一批输入，Swift 的 Pet2/Core 跑一遍，Python 的 pet2win/core 跑一遍，结果逐项比，一位都不差才算过。

用法（在 Pet2-Windows 文件夹里）：
  python difftest/difftest.py run       在 Mac 上：编 Swift（只读 ../Pet2/Core）、两边都跑、比较，并把输入和 Swift 的结果
                                         存进 difftest/golden/（Windows 上没有 Swift，就拿这份比）
  python difftest/difftest.py check     任何电脑：读 difftest/golden/，只跑 Python，和存着的 Swift 结果比

三类输入：
  scenario  一段段剧情：随机的姿势行、离开、多人、稍后、暂停、后端结果……每一步之后都记下画面，隔一阵记一次各种占比
  pure      纯函数：解析数字、去空格、拆 CSV、解析行、方向检查、读文件尾巴、桌宠说的话、同意规则、奖励……
  tail      CSVTail 跟着一串文件操作（写、追加、半行、清空、删掉、改名）走
"""
import base64
import gzip
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pet2win.core.backoff import PostureExit                       # noqa: E402
from pet2win.core.brain import (Away, Brain, Calm, Drifting, Lines, Paused, Reminding, Rewarding, Rule,  # noqa: E402
                                Settings, Snoozed, Waiting)
from pet2win.core.consent import Consent, ConsentChoice, ConsentRecord  # noqa: E402
from pet2win.core.csv_tail import CSVTail                          # noqa: E402
from pet2win.core.drowsiness import Drowsiness                     # noqa: E402
from pet2win.core import export_package                            # noqa: E402
from pet2win.core.onboarding import OnboardingState                # noqa: E402
from pet2win.core.posture_input import (ALL_ISSUES, AlertKind, AlertOutcome, AlertRow, Issue, PostureRow,  # noqa: E402
                                        Side)
from pet2win.core.profile import Profile                           # noqa: E402
from pet2win.core.rewards import CorrectionCounter, RewardKind, RewardRules  # noqa: E402
from pet2win.core.setup_check import DirectionCheck                # noqa: E402
from pet2win.core.swiftcompat import (split_newlines, swift_double, swift_int, swift_rounded,  # noqa: E402
                                      trim_whitespaces, trim_whitespaces_and_newlines)

GOLDEN = os.path.join(HERE, "golden")
BUILD = os.path.join(HERE, ".build")
PET2 = os.path.join(os.path.dirname(ROOT), "Pet2")
BASE = 1_793_000_000.0
TZ = "Asia/Shanghai"   # 两边用同一个时区算“今天第几次”


def enc(x):
    return format(struct.unpack("<Q", struct.pack("<d", float(x)))[0], "x")


def enc_opt(x):
    return None if x is None else enc(x)


def dec(s):
    return None if s is None else struct.unpack("<d", struct.pack("<Q", int(s, 16)))[0]


# ======================= 生成输入 =======================

SETTING_KEYS_FLOAT = ["windowCoverage", "coverageGapCap", "slouchRemindEyeDrop", "rearmShare", "uprightBubbleSeconds",
                      "sittingMinutes", "awayAfter", "leftSeatAfter", "sittingResetAfter", "correctedAfter",
                      "postureFollow", "sittingFollow", "snoozeSeconds", "sameIssueGap", "minGapBetweenReminders",
                      "quietAfterReminder", "rewardSeconds"]


def gen_settings(rng):
    variant = rng.choice(["default", "demo", "demo", "quiet", "fast", "random", "random"])
    spec = {"base": "demo" if variant == "demo" else "default"}
    if variant == "quiet":
        spec["set"] = {"sittingMinutes": enc(9999)}
        spec["upright"] = [enc(1200), enc(2)]
    elif variant == "fast":
        spec["allWindows"] = [enc(20), enc(99999)]
        spec["set"] = {"sameIssueGap": enc(0), "postureFollow": enc(1), "quietAfterReminder": enc(0),
                       "minGapBetweenReminders": enc(0)}
    elif variant == "random":
        if rng.random() < 0.5:
            spec["base"] = "demo"
        if rng.random() < 0.4:
            spec["allWindows"] = [enc(rng.choice([5, 10, 20, 30, 37.5, 60])), enc(rng.choice([20, 40, 61.5, 120])) if rng.random() < 0.5 else None]
        s = {}
        pool = {
            "windowCoverage": [0, 0.5, 0.8, 0.95, 1], "coverageGapCap": [1, 2.5, 5, 10], "slouchRemindEyeDrop": [0, 0.2, 0.4, 0.6],
            "rearmShare": [0, 0.1, 0.3, 0.5], "uprightBubbleSeconds": [5, 30, 60], "sittingMinutes": [1, 2, 2.5, 5, 45],
            "awayAfter": [3, 10, 15], "leftSeatAfter": [20, 60, 90], "sittingResetAfter": [30, 120, 300],
            "correctedAfter": [3, 10, 15.5], "postureFollow": [10, 60, 300], "sittingFollow": [30, 300, 600],
            "snoozeSeconds": [30, 120, 600], "sameIssueGap": [0, 30, 300, 1200], "minGapBetweenReminders": [0, 10, 60, 600],
            "quietAfterReminder": [0, 5, 30, 300], "rewardSeconds": [3, 8, 12.25],
        }
        for k in rng.sample(SETTING_KEYS_FLOAT, rng.randint(0, 8)):
            s[k] = enc(rng.choice(pool[k]))
        if rng.random() < 0.5:
            s["maxRemindersPerHour"] = rng.choice([0, 1, 2, 3, 60])
        if s:
            spec["set"] = s
        if rng.random() < 0.6:
            rules = {}
            for issue in rng.sample(ALL_ISSUES, rng.randint(1, 4)):
                rules[issue.value] = [enc(rng.choice([5, 10, 20, 30, 45.5, 60, 180])), enc(rng.choice([0.2, 0.5, 0.7, 0.8, 1.0])),
                                      rng.random() < 0.4]
            spec["rules"] = rules
        if rng.random() < 0.2:
            spec["removeRules"] = [i.value for i in rng.sample(ALL_ISSUES, rng.randint(1, 2))]
        if rng.random() < 0.4:
            spec["upright"] = [enc(rng.choice([10, 30, 40, 90])), enc(rng.choice([0.3, 0.6, 0.8, 0.99]))]
        if rng.random() < 0.4:
            secs = {k.value: enc(rng.choice([2, 7.8, 9.9, 12.5])) for k in rng.sample(list(RewardKind), rng.randint(0, 4))}
            spec["rewards"] = {"fast": enc(rng.choice([0, 10, 40, 100])), "heartEvery": rng.choice([0, 1, 2, 5]), "seconds": secs}
    return variant, spec


def gen_row_fields(rng, issues, side_sign=None, crowded=False):
    def side_value():
        if side_sign is not None:
            return side_sign * rng.choice([0.3, 2, 12, 18.5])
        return rng.choice([None, 0.0, -12.0, 12.0, -3.5, 4.25, 0.3])
    persons = None
    judged = True
    if crowded:
        persons = rng.choice([2, 3])
        judged = rng.choice([False, True])   # PostureRow 的 init 自己会把多人行改成没判断
    elif rng.random() < 0.15:
        persons = rng.choice([0, 1])
    if rng.random() < 0.02:
        judged = False
    eye = rng.choice([None, 0.1, 0.3, 0.399, 0.4, 0.5, 0.9]) if Issue.SLOUCH.value in issues or rng.random() < 0.2 else None
    return {"issues": issues, "severity": enc(rng.choice([0, 0.35, 0.7, 1])), "tilt": enc_opt(side_value()),
            "turn": enc_opt(side_value()), "shoulder": enc_opt(side_value()), "persons": persons, "judged": judged,
            "eyeDrop": enc_opt(eye)}


def gen_scenario(rng, idx):
    variant, spec = gen_settings(rng)
    long = variant in ("default", "quiet") or (variant == "random" and spec.get("base") == "default" and rng.random() < 0.5)
    duration = rng.choice([1800, 3600, 5400]) if long else rng.choice([120, 300, 600, 900])
    # 起点随机，有的故意放在本地零点前几分钟（测“今天第几次”过零点清零）
    t = BASE + rng.uniform(0, 5 * 86400)
    if rng.random() < 0.3:
        os.environ["TZ"] = TZ
        _tzset()
        lt = list(__import__("time").localtime(t))
        midnight = __import__("time").mktime((lt[0], lt[1], lt[2] + 1, 0, 0, 0, 0, 0, -1))
        t = midnight - rng.uniform(30, 600)
    end = t + duration
    step = rng.choice([1.0, 1.0, 2.0, 2.5, 0.5])
    ops = []
    mode = "good"
    mode_until = t
    bad_issues = []
    fraction = 1.0
    side_sign = None
    k = 0
    paused = False
    resume_at = None
    last_tick = t
    while t < end:
        if t >= mode_until:
            mode = rng.choices(["good", "bad", "bad", "away", "crowd", "flip"], [3, 4, 0, 1, 1, 1])[0]
            mode_until = t + rng.choice([10, 20, 40, 90, 200, 400, 700])
            bad_issues = [i.value for i in rng.sample(ALL_ISSUES, rng.choice([1, 1, 1, 2, 3]))]
            if rng.random() < 0.05:
                bad_issues = bad_issues + bad_issues[:1]   # 同一个姿势写了两遍
            fraction = rng.choice([0.3, 0.6, 0.72, 0.8, 0.85, 1.0, 1.0])
            side_sign = rng.choice([None, 1, -1, 1, -1])
        now = t + rng.choice([0, 0, 0, 0.25, 1.5])
        if mode == "away":
            pass   # 没有行，只走钟
        else:
            has = math.floor((k + 1) * fraction) > math.floor(k * fraction)
            if mode == "good":
                issues = []
            elif mode == "flip":
                issues = [Issue.HEAD_TILT.value] if has else []
                side_sign = 1 if k % 2 == 0 else -1
            else:
                issues = list(bad_issues) if has else []
            row_t = t
            if rng.random() < 0.01:
                row_t = t - rng.uniform(1, 30)   # 时间倒退的行
            ops.append(["row", enc(row_t), enc(now), gen_row_fields(rng, issues, side_sign, crowded=(mode == "crowd"))])
            k += 1
        if now - last_tick >= 1 or rng.random() < 0.5:
            ops.append(["tick", enc(now)])
            last_tick = now
        ops.append(["scene", enc(now)])
        r = rng.random()
        if r < 0.004:
            ops.append(["snooze", enc(now)])
        elif r < 0.006:
            ops.append(["cancelSnooze", enc(now)])
        elif r < 0.012:
            ops.append(["ack", enc(now)])
        elif r < 0.014 and not paused:
            ops.append(["pause", enc(now), True])
            paused = True
            resume_at = now + rng.choice([5, 30, 120])
        elif r < 0.02:
            ops.append(["presented", rng.randint(-3, 12), rng.random() < 0.5, enc(now)])
        elif r < 0.03:
            ops.append(["alert", enc(now), {"time": enc(now - rng.uniform(0, 300)), "kind": rng.choice(["posture", "sitting"]),
                                            "issue": rng.choice([None] + [i.value for i in ALL_ISSUES]),
                                            "outcome": rng.choice(["corrected", "left_seat", "stood_up", "not_corrected",
                                                                   "interrupted", "ended"]),
                                            "outcomeTime": enc(now)}])
        if paused and resume_at is not None and now >= resume_at:
            ops.append(["pause", enc(now), False])
            paused = False
        if k % 25 == 0 or rng.random() < 0.02:
            ops.append(["probe", enc(t)])
        t += step * rng.choice([1, 1, 1, 0.97, 1.03, 1.5])
    scenario = {"name": f"s{idx}-{variant}", "settings": spec, "ops": ops}
    if rng.random() < 0.3:
        scenario["inputStatus"] = rng.choice(["找不到 log.csv：体态检查开了吗？", "正在打开"])
    return scenario


def _tzset():
    import time
    if hasattr(time, "tzset"):
        time.tzset()


TRICKY_NUMBERS = ["", " ", "0", "-0", "+0", "1", "12", " 12", "12 ", "12.5", "-12.5", "+3", "1.", ".5", "-.5", ".", "+",
                  "1e3", "1E3", "1e+3", "1e-3", "1e", "e3", "1e400", "-1e400", "1e-400", "4e-320", "1_000", "0x10", "0X1A",
                  "0x1.8p1", "0x.8", "0x", "0xg", "inf", "-inf", "Infinity", "INF", "infinity", "+inf", "nan", "NaN", "-nan",
                  "nan(123)", "nan()", "snan", "nanx", "１２", "12\n", "\t12", "1,5", "1.2.3", "--1", "0012", "00.5",
                  "123456789012345678901234567890", "9223372036854775807", "9223372036854775808", "-9223372036854775808",
                  "-9223372036854775809", "2147483647", "2147483648", "-2147483648", "-2147483649", "0.1", "0.30000000000000004",
                  "1793000000.25", "3.4028236e38", "1e308", "1.7976931348623157e308", "1.7976931348623159e308", "abc",
                  "12abc", "٣", "1e3.5", "+-1", "0e0", "0x0p0", "0x1p-1075", "0x1p1024", "2.5e-324", "5e-324", "nan(0x10)",
                  "nan(abc)", "nan(99999999999999999999)", "-snan", "+nan", "NAN(5)", "nan(5", "nan(-5)", "nan(1_2)", "nan(00012)",
                  "nan(2251799813685248)", "SNaN", "snan(3)", "-0x0p0", "0x1.fffffffffffff8p1023", "1e-320", "-1e-400", "0x1P3",
                  "0x1.", "0x.", "1e+", "infinit", "infinityy", "-.e1", ".e1", "0x1p", "1.e5", "+.5e-1", "nan(1)x", "\u200b12",
                  "12\u200b", "\u00a012"]

TRICKY_TRIMS = ["\u200bw\u200b", "x\ufeff", "\u180ey", "", " a ", "\ta\t", "\na\n", "\r\na b\r\n", " x　", " y ", "\u0085z", "\x0bq\x0c", "​w",
                "  v ", "\x1ct\x1d", " \t \n ", "　中文　"]


def gen_pure(rng):
    def rand_line():
        parts = []
        for _ in range(rng.randint(0, 6)):
            parts.append(rng.choice(["a", "", "b c", '"x,y"', '"q""r"', '"', '""', "1.5", " ", '"open', 'mid"dle', "é", "中文"]))
        return rng.choice([",", ",,", ", "]).join(parts) if parts else ""

    rows = []
    for _ in range(400):
        d = {}
        for key in ["time", "issues", "severity", "persons", "tilt_to_left_deg", "turn_to_left", "left_shoulder_up_deg", "eye_drop"]:
            if rng.random() < 0.8:
                if key == "issues":
                    d[key] = rng.choice(["", "head_down", "head_down|shrug", " head_tilt | slouch ", "x|y", "|head_turn|",
                                         "slouch|lean_back|slide", "HEAD_DOWN", "head_down||shrug", "uneven_shoulders"])
                else:
                    d[key] = rng.choice(TRICKY_NUMBERS + ["1793000000.5", "100", "0.7", "2", "1", "-12.5", "8"])
        rows.append(d)
    alerts = []
    for _ in range(200):
        d = {}
        for key, pool in [("time", TRICKY_NUMBERS + ["100.00"]), ("kind", ["posture", "sitting", "Posture", ""]),
                          ("issue", ["head_down", "", "x", "head_tilt"]),
                          ("outcome", ["corrected", "left_seat", "stood_up", "not_corrected", "interrupted", "ended", "nope"]),
                          ("outcome_time", TRICKY_NUMBERS + ["123.00"])]:
            if rng.random() < 0.85:
                d[key] = rng.choice(pool)
        alerts.append(d)

    def rand_csv():
        header_pool = ["time", "label", "issues", "severity", "tilt_to_left_deg", "l_sh_y", "r_sh_y", "persons", "eye_drop"]
        cols = rng.sample(header_pool, rng.randint(1, len(header_pool)))
        if rng.random() < 0.1:
            cols = cols + [cols[0]]   # 重复的列名
        nl = rng.choice(["\n", "\r\n", "\n\n", "\r", " "])
        lines = [",".join(cols)]
        for _ in range(rng.randint(0, 30)):
            vals = []
            for c in cols:
                if c == "time":
                    vals.append(rng.choice([str(BASE + rng.randint(-20, 40)), f"{BASE + rng.random() * 30:.2f}", "", "x", " 1793000005"]))
                elif c == "issues":
                    vals.append(rng.choice(["", "head_down", "head_down|shrug", "slouch"]))
                elif c in ("l_sh_y", "r_sh_y"):
                    vals.append(rng.choice(["700", "1050", "1047.6", "", "x", " 300"]))
                elif c == "tilt_to_left_deg":
                    vals.append(rng.choice(["8", "-8", "2.9", "-3.1", "", "3", "-3", "nan"]))
                else:
                    vals.append(rng.choice(["0.7", "1", "2", "", "0"]))
            if rng.random() < 0.1:
                vals = vals[: rng.randint(0, len(vals))]
            lines.append(",".join(vals))
        text = nl.join(lines)
        if rng.random() < 0.7:
            text += nl
        if rng.random() < 0.1:
            text = nl + text
        return text

    evaluate = []
    for _ in range(300):
        since = BASE + rng.choice([-100, 0, 5, 10])
        until = rng.choice([None, BASE + 20, BASE + 1000])
        fh = rng.choice([None, 1080, 1440, 720.5])
        evaluate.append([rand_csv(), enc(since), enc_opt(until), enc_opt(fh)])
    rows_text = [[rand_csv(), enc(BASE + rng.choice([-100, 0, 5]))] for _ in range(200)]

    read_tail = []
    for _ in range(80):
        text = rand_csv()
        if rng.random() < 0.5:
            text = text * rng.randint(1, 200)
        raw = text.encode("utf-8")
        if rng.random() < 0.1:
            raw = raw + bytes([0xe4, 0xb8])   # 末尾半个汉字
        if rng.random() < 0.05:
            raw = bytes([0xff]) + raw
        read_tail.append([base64.b64encode(raw).decode("ascii"), rng.choice([16, 64, 100, 1000, 4096, 8192, 256 * 1024])])

    lines_in = []
    for issue in ALL_ISSUES:
        for side in [None, "left", "right"]:
            for w in [1, 5, 20, 30.7, 59.4, 59.5, 60, 89.9, 90, 120, 149.99, 150, 180, 210, 300, 600, 45.5]:
                lines_in.append([issue.value, side, enc(w)])
    consent = []
    for bits in range(128):
        flags = [(bits >> i) & 1 == 1 for i in range(7)]
        consent.append([{"time": enc(BASE + bits), "adult": flags[0], "c1": flags[1], "c2": flags[2], "c3": flags[3],
                         "c4": flags[4], "c5": flags[5], "c6": flags[6]}, rng.choice([None, "US", "CN", "other", "na"])])
    pick = []
    for _ in range(400):
        pick.append([rng.choice([None] + [i.value for i in ALL_ISSUES]), enc_opt(rng.choice([None, 0, 12, 39.9, 40, 40.1, 200])),
                     rng.randint(0, 21), rng.choice([0, 1, 2, 3, 5]), enc(rng.choice([0, 10, 40, 100]))])
    top = []
    for _ in range(100):
        tops = rng.sample(["p-1", "x", "y", "", "posture-data"], rng.randint(1, 2))
        entries = []
        for _ in range(rng.randint(0, 4)):
            tp = rng.choice(tops)
            entries.append(rng.choice([f"{tp}/a", f"{tp}/", f"/{tp}/b", f"{tp}", f"{tp}/c/d", f"{tp}//e"]))
        top.append([entries, rng.choice(["p-1.zip", "a.b.zip", "noext", ".zip", "p-9.ZIP"])])
    nan_bits = "7ff8000000000000"
    medians = [[rng.choice([enc(rng.choice([-9, -3, -2.9, 0, -0.0, 2.9, 3, 3.1, 8, 12.5])), nan_bits]) if rng.random() < 0.2
                else enc(rng.choice([-9, -3, -2.9, 0, -0.0, 2.9, 3, 3.1, 8, 12.5])) for _ in range(rng.randint(0, 70))]
               for _ in range(300)]
    return {
        "doubles": TRICKY_NUMBERS, "ints": TRICKY_NUMBERS, "trims": TRICKY_TRIMS,
        "splitNewlines": ["", "a", "a\nb", "a\r\nb", "a\r\rb", "\n\na\n", "a b c", "a\x0bb\x0cc", "a\x1cb", "a\u0085b",
                          "a\ńb", "x\r", "\r\n\r\n"],
        "csvSplit": [rand_line() for _ in range(400)] + ['a,"b,c",d', '"', '""', '"a"b', 'a,"', '"a""b",c'],
        "rows": rows, "alerts": alerts, "evaluate": evaluate, "rowsText": rows_text, "readTail": read_tail,
        "lines": lines_in, "sittingMinutes": [enc(x) for x in [1, 2, 2.5, 3.5, 44.5, 45, 45.49, 0.5]],
        "ageBands": [None, -1, 0, 17, 18, 24, 25, 34, 35, 44, 45, 54, 55, 64, 65, 129, 130, 200],
        "roundTo": [[enc(v), enc(s)] for v in [0, 2.5, 7.5, 12.5, 162.5, 163, 172, 173, 67.4, 52.6, -2.5, 181, 999.9] for s in [5, 1, 2.5]],
        "consent": consent, "pick": pick, "topFolder": top,
        "drowsiness": [enc(x) for x in [-1, 0, 0.39999, 0.4, 0.55, 0.69999, 0.7, 1, 5]],
        "median": medians,
        "pidRunning": [None, "", " ", "12345", "12345\n", " 12346 ", "0", "-5", "abc", "99999999999", "2147483647", "2147483648",
                       "+7", " 7 ", "12 34"],
        "permission": ["OpenCV: not authorized to capture video", "NOT AUTHORIZED", "没有权限", "没权限了", "等你允许使用摄像头", "允许使用摄像头",
                       "camera failed", ""],
        "dayKey": [enc(BASE + x) for x in [0, 3600 * 5.5, 3600 * 16, 3600 * 16.5, 86400, 86400 * 30.25]],
        "onboarding": [[d, enc_opt(at), r, enc(BASE + n)] for d in [False, True] for at in [None, BASE]
                       for r in [False, True] for n in [0, 3600, 86399.9, 86400, 172800]],
        "shouldersVisible": [[enc_opt(a), enc_opt(b), enc_opt(c)] for a in [None, 700, 1047.5, 1047.6, 1048] for b in [None, 1000]
                             for c in [None, 1440, 1080]],
    }


def gen_tail(rng):
    out = []
    for _ in range(60):
        ops = [["open"]]
        tails = 1
        header_pool = [["time", "label", "issues", "severity"], ["time", "issues", "label", "severity", "tilt_to_left_deg"],
                       ["time", "issues"], ["label", "time"]]
        for _ in range(rng.randint(5, 40)):
            r = rng.random()
            if r < 0.15:
                cols = rng.choice(header_pool)
                body = ",".join(cols) + rng.choice(["\n", "\r\n"])
                ops.append(["write", body if rng.random() < 0.8 else ""])
            elif r < 0.55:
                parts = []
                for _ in range(rng.randint(1, 4)):
                    parts.append(",".join(rng.choice(["1", "2.5", "head_down", "", "shrug", " time ", '"a,b"', "x"])
                                          for _ in range(rng.randint(1, 5))))
                text = rng.choice(["\n", "\r\n"]).join(parts)
                if rng.random() < 0.7:
                    text += "\n"
                ops.append(["append", text])
            elif r < 0.6:
                ops.append(["appendBytes", [0xe4, 0xb8, 0xad][: rng.randint(1, 3)]])
            elif r < 0.65:
                ops.append(["remove"])
            elif r < 0.68:
                ops.append(["rename", f"log-old-{rng.randint(1, 9)}.csv"])
            elif r < 0.72:
                ops.append(["open"])
                tails += 1
            else:
                ops.append(["poll", rng.randrange(tails)])
        ops.append(["poll", 0])
        out.append({"ops": ops})
    return out


def gen_anim(rng):
    """弹簧动作：一串串幕（换幕、强度变化、帧间隔忽长忽短），每帧比姿势。"""
    acts = [["waiting"], ["away"], ["calm"], ["snoozed"], ["paused"], ["reminding", 1], ["reminding", 2],
            ["remindingUpright", 3], ["noticed", 1], ["rewarding", 4], ["rewarding", 5]]
    for issue in ALL_ISSUES:
        for side in [None, "left", "right"]:
            acts.append(["drifting", issue.value, side])
    out = []
    for _ in range(40):
        now = rng.uniform(1000, 500000)
        frames = []
        for _ in range(rng.randint(3, 12)):
            a = rng.choice(acts)
            intensity = rng.choice([0, 0.2, 0.39, 0.4, 0.55, 0.7, 0.95, 1, 1.4])
            for _ in range(rng.randint(10, 260)):
                dt = rng.choice([1 / 60, 1 / 60, 1 / 30, 0.004, 0, 0.12, 1 / 60 * 1.07])
                now += dt if rng.random() > 0.002 else rng.uniform(1, 30)
                if a[0] == "drifting" and rng.random() < 0.05:
                    intensity = rng.choice([0.3, 0.45, 0.8, 1])
                frames.append([a, enc(intensity), enc(now), enc(dt)])
        out.append(frames)
    return out


def generate(seed=20261027, scenarios=220):
    rng = random.Random(seed)
    sc = [gen_scenario(rng, i) for i in range(scenarios)]
    pure = gen_pure(random.Random(seed + 1))
    tail = gen_tail(random.Random(seed + 2))
    anim = gen_anim(random.Random(seed + 3))
    return {"scenario": sc, "pure": pure, "tail": tail, "anim": anim}


# ======================= Python 这一边 =======================

def make_settings(spec):
    s = Settings.demo() if spec.get("base") == "demo" else Settings()
    aw = spec.get("allWindows")
    if aw is not None:
        s.set_all_windows(dec(aw[0]), dec(aw[1]))
    mapping = {"windowCoverage": "window_coverage", "coverageGapCap": "coverage_gap_cap",
               "slouchRemindEyeDrop": "slouch_remind_eye_drop", "rearmShare": "rearm_share",
               "uprightBubbleSeconds": "upright_bubble_seconds", "sittingMinutes": "sitting_minutes",
               "awayAfter": "away_after", "leftSeatAfter": "left_seat_after", "sittingResetAfter": "sitting_reset_after",
               "correctedAfter": "corrected_after", "postureFollow": "posture_follow", "sittingFollow": "sitting_follow",
               "snoozeSeconds": "snooze_seconds", "sameIssueGap": "same_issue_gap",
               "minGapBetweenReminders": "min_gap_between_reminders", "quietAfterReminder": "quiet_after_reminder",
               "rewardSeconds": "reward_seconds"}
    for k, v in (spec.get("set") or {}).items():
        if k == "maxRemindersPerHour":
            s.max_reminders_per_hour = v
        else:
            setattr(s, mapping[k], dec(v))
    for k, v in (spec.get("rules") or {}).items():
        s.rules[Issue(k)] = Rule(dec(v[0]), dec(v[1]), v[2])
    for k in spec.get("removeRules") or []:
        s.rules.pop(Issue(k), None)
    u = spec.get("upright")
    if u is not None:
        s.upright_rule = Rule(dec(u[0]), dec(u[1]))
    r = spec.get("rewards")
    if r is not None:
        if "fast" in r:
            s.rewards.fast_fix_seconds = dec(r["fast"])
        if "heartEvery" in r:
            s.rewards.heart_every = r["heartEvery"]
        if "seconds" in r:
            s.rewards.seconds = {RewardKind(k): dec(v) for k, v in r["seconds"].items()}
    return s


def encode_scene(scene):
    if isinstance(scene, Waiting):
        return ["waiting", scene.text]
    if isinstance(scene, Away):
        return ["away"]
    if isinstance(scene, Calm):
        return ["calm"]
    if isinstance(scene, Drifting):
        return ["drifting", scene.issue.value, scene.side.value if scene.side else None, enc(scene.intensity)]
    if isinstance(scene, Reminding):
        r = scene.reminder
        return ["reminding", r.id, r.kind.value, r.issue.value if r.issue else None, r.side.value if r.side else None, r.line,
                enc(r.started_at), r.bubble_hidden]
    if isinstance(scene, Rewarding):
        r = scene.reward
        return ["rewarding", r.id, r.issue.value if r.issue else None, r.line, enc(r.started_at), r.kind.value, r.today,
                enc(r.seconds)]
    if isinstance(scene, Snoozed):
        return ["snoozed", enc(scene.until)]
    if isinstance(scene, Paused):
        return ["paused"]
    raise TypeError(scene)


def probe(brain, t):
    out = ["probe", enc(t)]
    for issue in ALL_ISSUES:
        side = brain.same_side_share(issue, t)
        out.append([issue.value, enc_opt(brain.share(issue, t)), enc_opt(brain.share(issue, t, require_full_window=False)),
                    enc(brain.reminder_progress(issue, t)), [side[0].value, enc(side[1])] if side else None])
    out.append(["upright", enc_opt(brain.upright_share(t)), enc_opt(brain.upright_share(t, require_full_window=False))])
    lr = brain.latest_row
    out.append(["state", brain.paused, enc_opt(brain.snooze_until), enc_opt(lr.t if lr else None),
                enc_opt(brain.last_row_received_at), enc_opt(brain.last_crowded_at), brain.reminder_sees_correction])
    return out


def run_scenario(spec):
    brain = Brain(make_settings(spec["settings"]))
    if "inputStatus" in spec:
        brain.input_status = spec["inputStatus"]
    trace = []
    brain.on_event = lambda kind, details, t: trace.append(["event", kind, dict(details), enc(t)])
    for op in spec["ops"]:
        name = op[0]
        if name == "row":
            r = op[3]
            row = PostureRow.make(dec(op[1]), [Issue(x) for x in r["issues"]], severity=dec(r["severity"]),
                                  tilt_to_left=dec(r["tilt"]), turn_to_left=dec(r["turn"]), left_shoulder_up=dec(r["shoulder"]),
                                  persons=r["persons"], judged=r["judged"])
            row.eye_drop = dec(r["eyeDrop"])
            brain.ingest(row, now=dec(op[2]))
        elif name == "alert":
            a = op[2]
            brain.ingest_alert(AlertRow(alert_time=dec(a["time"]), kind=AlertKind(a["kind"]),
                                        issue=Issue(a["issue"]) if a["issue"] else None, outcome=AlertOutcome(a["outcome"]),
                                        outcome_time=dec(a["outcomeTime"])), now=dec(op[1]))
        elif name == "tick":
            brain.tick(dec(op[1]))
        elif name == "snooze":
            brain.snooze(dec(op[1]))
        elif name == "cancelSnooze":
            brain.cancel_snooze(dec(op[1]))
        elif name == "ack":
            brain.acknowledge(dec(op[1]))
        elif name == "pause":
            brain.set_paused(op[2], dec(op[1]))
        elif name == "presented":
            brain.reward_presented(op[1], op[2], dec(op[3]))
        elif name == "scene":
            trace.append(["scene", enc(dec(op[1])), encode_scene(brain.scene(dec(op[1])))])
        elif name == "probe":
            trace.append(probe(brain, dec(op[1])))
        else:
            raise ValueError(name)
    return trace


def enc_choice(c):
    return [enc(c.time), c.version, c.lang, c.adult, c.c1, c.c2, c.c3, c.c4, c.c5, c.c6]


def enc_row(r):
    if r is None:
        return None
    def side(i):
        s = r.side(i)
        return s.value if s else None
    return [enc(r.t), [i.value for i in r.issues], enc(r.severity), enc_opt(r.tilt_to_left), enc_opt(r.turn_to_left),
            enc_opt(r.left_shoulder_up), r.persons, enc_opt(r.eye_drop), r.judged,
            side(Issue.HEAD_TILT), side(Issue.HEAD_TURN), side(Issue.UNEVEN_SHOULDERS)]


def run_pure(inp):
    out = {}
    out["doubles"] = [enc_opt(swift_double(s)) for s in inp["doubles"]]
    out["ints"] = [swift_int(s) for s in inp["ints"]]
    out["int32s"] = [swift_int(s, bits=32) for s in inp["ints"]]
    out["trims"] = [[trim_whitespaces(s), trim_whitespaces_and_newlines(s)] for s in inp["trims"]]
    out["splitNewlines"] = [split_newlines(s) for s in inp["splitNewlines"]]
    out["csvSplit"] = [CSVTail.split(s) for s in inp["csvSplit"]]
    out["rows"] = [enc_row(PostureRow.from_csv(d)) for d in inp["rows"]]
    alerts = []
    for d in inp["alerts"]:
        a = AlertRow.from_csv(d)
        alerts.append(None if a is None else [enc(a.alert_time), a.kind.value, a.issue.value if a.issue else None,
                                              a.outcome.value, enc(a.outcome_time)])
    out["alerts"] = alerts
    ev = []
    for text, since, until, fh in inp["evaluate"]:
        r = DirectionCheck.evaluate(text, dec(since), dec(until) if until is not None else float("inf"), dec(fh))
        ev.append([r.mirror.value, r.shoulders_visible, r.rows])
    out["evaluate"] = ev
    out["rowsText"] = [[enc_row(r) for r in DirectionCheck.rows(text, dec(since))] for text, since in inp["rowsText"]]
    tmp = os.path.join(tempfile.gettempdir(), f"pet2-difftest-{uuid.uuid4()}")
    os.makedirs(tmp)
    try:
        tails = []
        for content, nbytes in inp["readTail"]:
            path = os.path.join(tmp, "log.csv")
            with open(path, "wb") as f:
                f.write(base64.b64decode(content))
            tails.append(DirectionCheck.read_tail(path, nbytes))
        out["readTail"] = tails
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out["lines"] = [Lines.reminder(Issue(a[0]), Side(a[1]) if a[1] else None, dec(a[2])) for a in inp["lines"]]
    out["sittingLines"] = [Lines.sitting(int(swift_rounded(dec(s)))) for s in inp["sittingMinutes"]]
    out["ageBands"] = [Profile.age_band(a) for a in inp["ageBands"]]
    out["roundTo"] = [Profile.round_to(dec(a[0]), dec(a[1])) for a in inp["roundTo"]]
    consent_out = []
    for d, country in inp["consent"]:
        c = ConsentChoice(time=dec(d["time"]), adult=d["adult"], c1=d["c1"], c2=d["c2"], c3=d["c3"], c4=d["c4"], c5=d["c5"], c6=d["c6"])
        n = Consent.normalized(c, country)
        record = ConsentRecord()
        first = Consent.record(c, country, record)
        flipped = c.copy(); flipped.c2 = not c.c2
        second = Consent.record(flipped, country, record)
        consent_out.append([enc_choice(n), Consent.can_use(n), Consent.can_export(c), Consent.asks_pain(c), Consent.asks_points(c),
                            Consent.export_arguments(c, True, True), Consent.export_arguments(c, False, True),
                            Consent.export_arguments(n, True, False), first, second, [enc_choice(h) for h in record.history]])
    out["consent"] = consent_out
    picks = []
    for issue_raw, secs, today, heart_every, fast in inp["pick"]:
        rules = RewardRules()
        rules.heart_every = heart_every
        rules.fast_fix_seconds = dec(fast)
        issue = Issue(issue_raw) if issue_raw else None
        kind = rules.pick(issue, dec(secs), today)
        picks.append([kind.value, RewardRules.line(kind, issue, today)])
    out["pick"] = picks
    out["topFolder"] = [export_package.top_folder(a[0], a[1]) for a in inp["topFolder"]]
    out["drowsiness"] = [Drowsiness.stage(dec(x)).value for x in inp["drowsiness"]]
    out["median"] = [enc_opt(DirectionCheck.median([dec(x) for x in m])) for m in inp["median"]]
    out["mirror"] = [DirectionCheck.mirror([dec(x) for x in m]).value for m in inp["median"]]
    out["pidRunning"] = [PostureExit.someone_else_running(v, lambda p: p % 2 == 1) for v in inp["pidRunning"]]
    out["permission"] = [PostureExit.is_permission_problem(s) for s in inp["permission"]]
    out["dayKey"] = [CorrectionCounter.day_key(dec(x)) for x in inp["dayKey"]]
    ob = []
    for done, at, reminded, now in inp["onboarding"]:
        s = OnboardingState()
        s.done = done
        s.selftest_deferred_at = dec(at)
        s.selftest_reminded = reminded
        ob.append(s.should_remind_selftest(dec(now)))
    out["onboarding"] = ob
    out["shouldersVisible"] = [DirectionCheck.shoulders_visible(dec(a), dec(b), dec(c)) for a, b, c in inp["shouldersVisible"]]
    return out


def run_tail(spec):
    d = os.path.join(tempfile.gettempdir(), f"pet2-difftail-{uuid.uuid4()}")
    os.makedirs(d)
    path = os.path.join(d, "log.csv")
    tails = []
    trace = []
    try:
        for op in spec["ops"]:
            name = op[0]
            if name == "open":
                tails.append(CSVTail(path))
            elif name == "write":
                with open(path, "wb") as f:
                    f.write(op[1].encode("utf-8"))
            elif name == "append":
                with open(path, "ab") as f:
                    f.write(op[1].encode("utf-8"))
            elif name == "appendBytes":
                if os.path.exists(path):
                    with open(path, "ab") as f:
                        f.write(bytes(op[1]))
            elif name == "remove":
                try:
                    os.remove(path)
                except OSError:
                    pass
            elif name == "rename":
                try:
                    os.replace(path, os.path.join(d, op[1])) if not os.path.exists(os.path.join(d, op[1])) else None
                except OSError:
                    pass
            elif name == "poll":
                tail = tails[op[1]]
                rows = tail.poll()
                st = tail.status
                status = ["reading", st.rows] if st.kind == "reading" else st.kind
                trace.append([rows, status, dict(tail.columns)])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return trace


def run_anim(seqs):
    from pet2win.app import animator as A
    out = []
    for seq in seqs:
        anim = A.Animator()
        frames = []
        for a, intensity, now, dt in seq:
            kind = a[0]
            if kind == "drifting":
                act = A.drifting_act(Issue(a[1]), Side(a[2]) if a[2] else None)
            elif kind in ("reminding", "remindingUpright", "noticed", "rewarding"):
                act = A.Act(kind, id=a[1])
            else:
                act = A.Act(kind)
            anim.step(act, dec(intensity), dec(now), dec(dt))
            frames.append([enc(getattr(anim.pose, k)) for k in A.POSE_KEYS] + [enc(anim.squash), enc(anim.doze)])
        out.append(frames)
    return out


def run_python(inputs):
    os.environ["TZ"] = TZ
    _tzset()
    return {"scenario": [run_scenario(s) for s in inputs["scenario"]], "pure": run_pure(inputs["pure"]),
            "tail": [run_tail(t) for t in inputs["tail"]], "anim": run_anim(inputs["anim"])}


# ======================= Swift 这一边 =======================

def build_swift():
    os.makedirs(os.path.join(BUILD, "src"), exist_ok=True)
    shutil.copy(os.path.join(HERE, "trace.swift"), os.path.join(BUILD, "src", "main.swift"))
    core = sorted(os.path.join(PET2, "Core", f) for f in os.listdir(os.path.join(PET2, "Core")) if f.endswith(".swift"))
    exe = os.path.join(BUILD, "trace")
    sdk = os.environ.get("SDK_PATH", "/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk")
    cmd = ["swiftc", "-sdk", sdk, "-target", "arm64-apple-macosx26.0", "-module-cache-path", os.path.join(BUILD, "ModuleCache"),
           "-O"] + core + [os.path.join(BUILD, "src", "main.swift"), "-o", exe]
    subprocess.run(cmd, check=True)
    return exe


def build_swift_anim():
    os.makedirs(os.path.join(BUILD, "anim-src"), exist_ok=True)
    shutil.copy(os.path.join(HERE, "anim.swift"), os.path.join(BUILD, "anim-src", "main.swift"))
    core = sorted(os.path.join(PET2, "Core", f) for f in os.listdir(os.path.join(PET2, "Core")) if f.endswith(".swift"))
    mac = [os.path.join(PET2, "Mac", "Animator.swift"), os.path.join(PET2, "Mac", "WhaleRig.swift")]
    exe = os.path.join(BUILD, "anim")
    sdk = os.environ.get("SDK_PATH", "/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk")
    cmd = ["swiftc", "-sdk", sdk, "-target", "arm64-apple-macosx26.0", "-module-cache-path", os.path.join(BUILD, "ModuleCache"),
           "-O", "-framework", "AppKit"] + core + mac + [os.path.join(BUILD, "anim-src", "main.swift"), "-o", exe]
    subprocess.run(cmd, check=True)
    return exe


def run_swift(exe, inputs):
    out = {}
    env = dict(os.environ, TZ=TZ)
    with tempfile.TemporaryDirectory() as tmp:
        for kind in ("scenario", "pure", "tail"):
            p = os.path.join(tmp, f"{kind}.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(inputs[kind], f, ensure_ascii=False)
            res = subprocess.run([exe, kind, p], capture_output=True, env=env, check=True)
            out[kind] = json.loads(res.stdout.decode("utf-8"))
        p = os.path.join(tmp, "anim.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(inputs["anim"], f)
        res = subprocess.run([build_swift_anim(), p], capture_output=True, env=env, check=True)
        out["anim"] = json.loads(res.stdout.decode("utf-8"))
    return out


# ======================= 比较 =======================

def strict_equal(a, b):
    if type(a) is bool or type(b) is bool:
        return type(a) is bool and type(b) is bool and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(strict_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def describe(x):
    s = json.dumps(x, ensure_ascii=False)
    return s if len(s) < 600 else s[:600] + "…"


def compare(inputs, swift, py):
    problems = []
    counts = {}
    for i, (s, p) in enumerate(zip(swift["scenario"], py["scenario"])):
        counts["scenario"] = counts.get("scenario", 0) + len(s)
        if len(s) != len(p):
            problems.append(f"剧情 {inputs['scenario'][i]['name']}：条数不一样 swift={len(s)} python={len(p)}")
        for j, (a, b) in enumerate(zip(s, p)):
            if not strict_equal(a, b):
                problems.append(f"剧情 {inputs['scenario'][i]['name']} 第 {j} 条：\n  swift  {describe(a)}\n  python {describe(b)}")
                break
    for key in swift["pure"]:
        a_list, b_list = swift["pure"][key], py["pure"].get(key)
        counts["pure"] = counts.get("pure", 0) + len(a_list)
        if b_list is None or len(a_list) != len(b_list):
            problems.append(f"纯函数 {key}：条数不一样")
            continue
        src = inputs["pure"].get(key) or inputs["pure"].get({"int32s": "ints", "mirror": "median", "sittingLines": "sittingMinutes"}.get(key, key))
        for j, (a, b) in enumerate(zip(a_list, b_list)):
            if not strict_equal(a, b):
                problems.append(f"纯函数 {key} 第 {j} 个（输入 {describe(src[j]) if src else '?'}）：\n  swift  {describe(a)}\n  python {describe(b)}")
    exact = sys.platform == "darwin"
    for i, (s, p) in enumerate(zip(swift.get("anim", []), py.get("anim", []))):
        counts["anim"] = counts.get("anim", 0) + len(s)
        for j, (a, b) in enumerate(zip(s, p)):
            if exact:
                same = a == b
            else:
                same = all(abs(dec(x) - dec(y)) <= 1e-6 for x, y in zip(a, b))
            if not same:
                problems.append(f"动作 第 {i} 段 第 {j} 帧：\n  swift  {[dec(x) for x in a]}\n  python {[dec(x) for x in b]}")
                break
    for i, (s, p) in enumerate(zip(swift["tail"], py["tail"])):
        counts["tail"] = counts.get("tail", 0) + len(s)
        if not strict_equal(s, p):
            j = next((k for k, (a, b) in enumerate(zip(s, p)) if not strict_equal(a, b)), min(len(s), len(p)))
            problems.append(f"读文件 第 {i} 段 第 {j} 次 poll：\n  swift  {describe(s[j] if j < len(s) else None)}\n"
                            f"  python {describe(p[j] if j < len(p) else None)}")
    return problems, counts


def save_gz(path, obj):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def load_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if cmd == "run":
        inputs = generate()
        exe = build_swift()
        swift = run_swift(exe, inputs)
        py = run_python(inputs)
        os.makedirs(GOLDEN, exist_ok=True)
        save_gz(os.path.join(GOLDEN, "inputs.json.gz"), inputs)
        save_gz(os.path.join(GOLDEN, "swift.json.gz"), swift)
    elif cmd == "check":
        inputs = load_gz(os.path.join(GOLDEN, "inputs.json.gz"))
        swift = load_gz(os.path.join(GOLDEN, "swift.json.gz"))
        py = run_python(inputs)
    else:
        print(__doc__)
        return 2
    problems, counts = compare(inputs, swift, py)
    summary = "，".join(f"{k} {v} 条" for k, v in counts.items())
    if problems:
        for p in problems[:30]:
            print("✗", p)
        print(f"✗ 对拍没过：{len(problems)} 处不一样（共比了 {summary}）")
        return 1
    print(f"✓ 对拍通过：Swift 和 Python 一模一样（{len(inputs['scenario'])} 段剧情，比了 {summary}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
