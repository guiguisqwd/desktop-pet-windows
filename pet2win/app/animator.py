"""照翻 Pet2/Mac/Animator.swift（加上 WhaleRig.swift 里的 RigPose）：让动作不僵。只用标准库，不碰 Qt。

每个关节不是直接跳到目标姿势，而是像弹簧一样被拉过去，会有惯性、过冲和回弹；尾巴更软、跟得更慢，头比身子晚半拍。
每一幕只给“目标姿势”（随时间变化），弹簧负责中间的过渡，所以任何两幕之间切换都是连续的。
算式的写法和先后顺序都照 Swift，在 Mac 上和 Swift 版逐帧对拍过（difftest/anim）。
"""
import math
from dataclasses import dataclass, field, fields

from pet2win.core.drowsiness import Drowsiness
from pet2win.core.posture_input import Issue, Side
from pet2win.core.swiftcompat import swift_max, swift_min

POSE_KEYS = ["rootX", "rootY", "bodyAngle", "headAngle", "chestAngle", "chestLift", "shoulderLift", "leftArm", "rightArm",
             "leftForearm", "rightForearm", "tailAngle", "bodyLift", "leftFootLift", "rightFootLift", "blink", "smile",
             "mouthOpen", "cheek", "exhale", "sign", "signLift", "opacity"]


@dataclass
class RigPose:
    """一张原画切块的姿势（WhaleRig.swift 的 RigPose）。字段名照 Swift，方便对照。"""
    rootX: float = 0.0
    rootY: float = 0.0
    bodyAngle: float = 0.0
    headAngle: float = 0.0
    chestAngle: float = 0.0
    chestLift: float = 0.0
    shoulderLift: float = 0.0
    leftArm: float = 0.0
    rightArm: float = 0.0
    leftForearm: float = 0.0
    rightForearm: float = 0.0
    tailAngle: float = 0.0
    bodyLift: float = 0.0
    leftFootLift: float = 0.0
    rightFootLift: float = 0.0
    blink: float = 0.0
    smile: float = 0.0
    mouthOpen: float = 0.0
    cheek: float = 0.0
    exhale: float = 0.0
    sign: float = 0.0
    signLift: float = 0.0
    opacity: float = 1.0

    def copy(self):
        return RigPose(**{f.name: getattr(self, f.name) for f in fields(self)})


class Spring:
    def __init__(self, x=0.0, frequency=1.0, damping=1.0):
        self.x = float(x)
        self.v = 0.0
        self.frequency = frequency   # 每秒摆几次：越大跟得越紧
        self.damping = damping       # 1 = 不回弹；越小回弹越多

    def step(self, target, dt):
        w = 2 * math.pi * self.frequency
        # 小步积分，保持稳定。
        steps = max(1, int(math.ceil(dt / (1.0 / 240))))
        h = dt / steps
        for _ in range(steps):
            a = w * w * (target - self.x) - 2 * self.damping * w * self.v
            self.v += a * h
            self.x += self.v * h


@dataclass
class Targets:
    pose: RigPose = field(default_factory=RigPose)
    squash: float = 0.0   # 整体竖直方向的压扁（负）/拉长（正），以脚底为中心
    doze: float = 0.0     # 头顶冒“z”的程度（0–1）


@dataclass(frozen=True)
class Act:
    """一幕的种类。换幕时重新计时。kind：waiting / away / calm / snoozed / paused / drifting / reminding /
    remindingUpright / noticed / rewarding。"""
    kind: str
    issue: object = None
    side: object = None
    id: object = None


WAITING, AWAY, CALM, SNOOZED, PAUSED = Act("waiting"), Act("away"), Act("calm"), Act("snoozed"), Act("paused")


def drifting_act(issue, side):
    return Act("drifting", issue=issue, side=side)


def reminding_act(id_):
    return Act("reminding", id=id_)


def reminding_upright_act(id_):
    return Act("remindingUpright", id=id_)


def noticed_act(id_):
    return Act("noticed", id=id_)


def rewarding_act(id_):
    return Act("rewarding", id=id_)


CHANNELS = [
    # 关节, 频率, 阻尼
    ("rootX", 1.8, 0.7), ("rootY", 3.2, 0.42),
    ("bodyAngle", 2.0, 0.5), ("bodyLift", 2.6, 0.45),
    ("chestAngle", 2.2, 0.5), ("chestLift", 2.6, 0.5), ("shoulderLift", 3.0, 0.55),
    ("headAngle", 1.7, 0.42),
    ("leftArm", 2.4, 0.5), ("rightArm", 2.2, 0.5),
    ("leftForearm", 2.0, 0.45), ("rightForearm", 1.9, 0.45),
    ("tailAngle", 1.3, 0.28),
    ("leftFootLift", 5, 0.7), ("rightFootLift", 5, 0.7),
    ("blink", 14, 1), ("smile", 4, 0.9), ("mouthOpen", 10, 0.8),
    ("cheek", 2, 1), ("exhale", 5, 0.9), ("opacity", 2.5, 1),
]

# 钻地时往下沉多少：只露出头顶的呆毛和头饰。
BURROW_DEPTH = 178.0
# 奖励前半段：蹲一下、快速伸一下懒腰，然后换成奖励 GIF。
STRETCH_SECONDS = 0.6


class Animator:
    def __init__(self):
        self.pose = RigPose()
        self.squash = 0.0
        self.doze = 0.0
        self._act = WAITING
        self._act_start = 0.0
        # 用户最近一次歪头往哪边（屏幕方向：-1 左，+1 右）。还没歪过就往右。
        self._last_lean = 1.0
        self._springs = {key: Spring(1 if key == "opacity" else 0, f, d) for key, f, d in CHANNELS}
        self._squash_spring = Spring(0, 4, 0.45)
        self._doze_spring = Spring(0, 1.5, 1)
        self.pose.opacity = 1

    def step(self, act, intensity, now, dt):
        """每帧一次：now 是秒（单调递增），dt 是离上一帧多久。"""
        if act != self._act:
            self._act = act
            self._act_start = now
        if self._act.kind == "drifting" and self._act.issue == Issue.HEAD_TILT and self._act.side is not None:
            self._last_lean = -1.0 if self._act.side == Side.LEFT else 1.0
        age = now - self._act_start
        target = targets(self._act, float(intensity), self._last_lean, age, now)

        # 跟随：身子一晃，尾巴往反方向甩；身子往上窜，尾巴往下压。
        body_v = self._springs["bodyAngle"].v
        lift_v = self._springs["rootY"].v + self._springs["bodyLift"].v
        target.pose.tailAngle += -0.35 * body_v - 0.12 * lift_v
        target.pose.headAngle += -0.05 * body_v
        # 跳起来的时候被拉长，落地的时候被压扁。
        target.squash += swift_max(-0.05, swift_min(0.05, 0.0035 * self._springs["rootY"].v))

        d = float(swift_min(0.05, swift_max(0, dt)))
        for key, _, _ in CHANNELS:
            spring = self._springs[key]
            spring.step(getattr(target.pose, key), d)
            setattr(self.pose, key, spring.x)
        self._squash_spring.step(target.squash, d)
        self._doze_spring.step(target.doze, d)
        self.squash = swift_max(-0.08, swift_min(0.08, self._squash_spring.x))
        self.doze = swift_max(0, swift_min(1, self._doze_spring.x))
        self.pose = bounded(self.pose)


def bounded(source):
    """原画能承受的范围（头和眼睛不变形，再大就露馅）。"""
    p = source.copy()
    p.headAngle = swift_min(4.5, swift_max(-4.5, p.headAngle))
    p.bodyAngle = swift_min(7, swift_max(-7, p.bodyAngle))
    p.chestAngle = swift_min(5, swift_max(-5, p.chestAngle))
    p.chestLift = swift_min(3.5, swift_max(0, p.chestLift))
    p.shoulderLift = swift_min(2, swift_max(0, p.shoulderLift))
    p.bodyLift = swift_min(3, swift_max(-6, p.bodyLift))
    p.rootY = swift_max(-BURROW_DEPTH - 10, p.rootY)
    p.tailAngle = swift_min(14, swift_max(-14, p.tailAngle))
    for key in ("blink", "smile", "opacity", "mouthOpen", "cheek", "exhale"):
        setattr(p, key, swift_min(1, swift_max(0, getattr(p, key))))
    p.leftFootLift = swift_max(0, p.leftFootLift)
    p.rightFootLift = swift_max(0, p.rightFootLift)
    p.sign = 0
    return p


# ---------- 每一幕的目标 ----------

def wave(t, period, phase=0.0):
    return math.sin((t / period + phase) * 2 * math.pi)


def bump(t, start, rise, hold, fall):
    """0→1→0 的一个包络：start 开始，rise 秒升满，hold 秒保持，fall 秒降回。"""
    x = t - start
    if x <= 0:
        return 0.0
    if x < rise:
        return smooth(x / rise)
    if x < rise + hold:
        return 1.0
    if x < rise + hold + fall:
        return 1 - smooth((x - rise - hold) / fall)
    return 0.0


def smooth(x):
    t = swift_min(1, swift_max(0, x))
    return t * t * (3 - 2 * t)


def blink(now):
    """自然眨眼：每隔几秒一次，间隔不完全一样。"""
    period = 4.3
    k = math.floor(now / period)
    jitter = math.fmod(math.sin(k * 12.9898) * 43758.5453, 1)
    at = k * period + abs(jitter) * 1.5
    return bump(now, at, 0.06, 0.05, 0.1)


def calm(age, now):
    t = Targets()
    breath = wave(now, 3.6)
    t.pose.chestLift = 0.9 + 0.7 * breath
    t.pose.shoulderLift = 0.35 + 0.3 * breath
    t.pose.bodyLift = 0.45 * breath
    t.pose.bodyAngle = 0.9 * wave(now, 7.7, 0.2)
    t.pose.headAngle = 1.1 * wave(now, 6.1)
    t.pose.tailAngle = 4.5 * wave(now, 2.9)
    t.pose.leftArm = 2 + 1.5 * breath
    t.pose.rightArm = -2 - 1.5 * breath
    t.pose.smile = 0.3
    t.pose.rootX = 1.2 * wave(now, 11)
    # 隔一阵左右看一眼，再回来。
    glance_cycle = math.fmod(now, 9.5)
    side = 1.0 if int(now / 9.5) % 2 == 0 else -1.0
    t.pose.headAngle += side * 2.6 * bump(glance_cycle, 5, 0.25, 1.1, 0.35)
    t.pose.blink = blink(now)
    return t


def targets(act, i, lean=1.0, age=0.0, now=0.0):
    """gui 10-27：“只把歪头绑定歪头”。只有歪头这个姿势会歪头；其余每一幕头和身子都是正的。"""
    t = raw_targets(act, i, lean, age, now)
    tilt_allowed = False
    if act.kind == "drifting" and act.issue == Issue.HEAD_TILT:
        tilt_allowed = True
    elif act.kind == "remindingUpright":
        # 扭腰时身子可以转一点，但头要正：头反着转回来，屏幕上看头不歪。
        t.pose.headAngle = -(t.pose.bodyAngle + t.pose.chestAngle)
        return t
    if not tilt_allowed:
        t.pose.headAngle = 0.0
        t.pose.bodyAngle = 0.0
        t.pose.chestAngle = 0.0
    return t


def raw_targets(act, i, lean, age, now):
    k = act.kind
    if k == "calm":
        return calm(age, now)
    if k == "waiting":
        t = calm(age, now)
        t.pose.opacity = 0.8
        return t
    if k == "away":
        # 人不见了：东张西望。
        t = Targets()
        t.pose.headAngle = 3 * wave(now, 3.2)
        t.pose.bodyAngle = 1.5 * wave(now, 3.2, 0.1)
        t.pose.rootX = 3 * wave(now, 6.4)
        t.pose.chestLift = 1 + 0.4 * wave(now, 3.6)
        t.pose.tailAngle = 3 * wave(now, 1.6)
        t.pose.leftArm = 4.0
        t.pose.rightArm = -4.0
        t.pose.blink = blink(now)
        return t
    if k == "drifting":
        return drifting(act.issue, act.side, i, age, now)
    if k == "remindingUpright":
        return reminding_upright(age, now)
    if k == "noticed":
        # “注意到了”：轻轻一蹦、挺起来、睁大眼睛笑，尾巴小摇。
        t = Targets()
        t.pose.rootY = 4 * bump(age, 0, 0.12, 0.06, 0.15)
        t.pose.bodyLift = 0.8
        t.pose.chestLift = 2.2 + 0.3 * wave(now, 2.4)
        t.pose.shoulderLift = 0.6
        t.pose.smile = 0.85
        t.pose.cheek = 0.35
        t.pose.blink = 0.0
        t.pose.tailAngle = 5 * wave(now, 0.7)
        return t
    if k == "reminding":
        return reminding(age, now)
    if k == "rewarding":
        return rewarding(age, now, i)
    if k == "snoozed":
        # “稍后”：钻进地里躲起来，只露头顶；隔一会儿探一下头。
        t = Targets()
        peek_phase = math.fmod(age, 7.5)
        peek = bump(peek_phase, 4.5, 0.35, 0.9, 0.45)
        dive = bump(age, 0, 0.12, 0.05, 0.12)   # 先往上一窜再钻下去
        t.pose.rootY = -BURROW_DEPTH + 9 * peek + 6 * dive
        t.squash = 0.04 * dive
        t.pose.leftArm = 4.0
        t.pose.rightArm = -4.0
        t.pose.tailAngle = 4 * wave(now, 1.2) * peek
        t.pose.blink = blink(now)
        return t
    # paused
    t = Targets()
    t.pose.blink = 1.0
    t.pose.opacity = 0.45
    return t


def drifting(issue, side, progress, age, now):
    """“下一步”：比用户现在的样子再塌一点、再累一点。方向跟着用户（像照镜子）。"""
    i = swift_min(1, swift_max(0, progress))
    t = Targets()
    # 进入时的一下“泄气”：先吸一口气耸起来，再塌下去。
    deflate = bump(age, 0, 0.35, 0.1, 0.5)
    breath = wave(now, 4.6)
    t.pose.chestLift = 0.2 + 0.25 * breath + 1.6 * deflate
    t.pose.shoulderLift = 1.4 * deflate
    t.pose.bodyLift = -(2 + 3.2 * i) * (1 - deflate) + 0.3 * breath
    t.pose.leftArm = 4.0
    t.pose.rightArm = -4.0
    t.pose.leftForearm = 3.0
    t.pose.rightForearm = -3.0
    t.pose.tailAngle = -7 + 1.5 * wave(now, 4.6, 0.4)
    t.pose.smile = 0.0

    if issue in (Issue.HEAD_DOWN, Issue.FORWARD_HEAD, Issue.SLOUCH, Issue.LEAN_BACK, Issue.SLIDE):
        t.pose.bodyLift -= 0.8 * i
        t.pose.blink = blink(now)
    elif issue == Issue.SHRUG:
        t.pose.shoulderLift = 2.0
        t.pose.chestLift = 1.5 + 0.3 * breath
        t.pose.bodyLift = -1.5 * i
        t.pose.leftArm = -3.0
        t.pose.rightArm = 3.0
        t.pose.blink = blink(now)
    elif issue == Issue.HEAD_TILT:
        # 只有歪头会歪头：方向跟用户（像照镜子：本人往左，它往屏幕左边）。
        m = -1.0 if side == Side.LEFT else 1.0
        t.pose.headAngle = (3.2 + 1.3 * i) * m
        t.pose.blink = blink(now)
    elif issue == Issue.UNEVEN_SHOULDERS:
        t.pose.blink = blink(now)
    elif issue == Issue.HEAD_TURN:
        m = -1.0 if side == Side.LEFT else 1.0
        t.pose.rootX = 3 * m
        t.pose.blink = blink(now)
    # 犯困 / 睡着：只看这个姿势离提醒门槛多远。头始终正着。
    stage = Drowsiness.stage(float(progress))
    if stage == Drowsiness.DROWSY:
        heavy = bump(math.fmod(age, 3.2), 0.6, 0.35, 0.7, 0.45)
        nod_phase = math.fmod(age + 1, 5.5)
        nod = bump(nod_phase, 2.5, 1.2, 0.3, 0.08)
        t.pose.blink = swift_max(swift_max(t.pose.blink, heavy), 1.0 if nod > 0.5 else 0.0)
        t.pose.bodyLift -= 1.6 + 1.8 * nod
        t.pose.shoulderLift = 0.0
        t.squash -= 0.02 * nod
    elif stage == Drowsiness.ASLEEP:
        breath = wave(now, 5)
        t.pose.blink = 1.0
        t.pose.bodyLift = -(3.4 + 0.7 * breath)
        t.pose.chestLift = 0.4 + 0.5 * breath
        t.pose.shoulderLift = 0.0
        t.pose.tailAngle = -9.0
        t.doze = 1.0
    # 隔一会儿叹口气。
    sigh_phase = math.fmod(age + 3, 8)
    sigh = bump(sigh_phase, 0, 0.4, 0.4, 0.6)
    t.pose.exhale = 0.7 * sigh
    t.pose.mouthOpen = 0.25 * sigh
    t.pose.shoulderLift += 0.8 * bump(sigh_phase, -0.3, 0.3, 0.1, 0.5)
    return t


def reminding_upright(a, now):
    """坐直太久的提醒：站起来 → 双手举高伸展 → 左右扭一扭腰 → 回来，循环。不歪头。"""
    t = Targets()
    cycle = 6.0
    p = math.fmod(a, cycle)
    crouch = bump(p, 0, 0.15, 0.05, 0.15)
    up = bump(p, 0.2, 0.35, 4.6, 0.6)
    reach = bump(p, 0.5, 0.45, 1.2, 0.5)
    twist = bump(p, 2.6, 0.3, 1.8, 0.4)
    t.pose.rootY = 7 * up
    t.pose.bodyLift = -2.5 * crouch + 1.2 * up
    t.squash = -0.05 * crouch + 0.05 * reach
    t.pose.leftArm = 82 * reach + 6 * twist + 2
    t.pose.rightArm = -85 * reach - 6 * twist - 2
    t.pose.leftForearm = 18 * reach
    t.pose.rightForearm = -19 * reach
    t.pose.chestLift = 0.8 + 2.6 * reach + 0.6 * up
    t.pose.shoulderLift = 1.5 * reach
    swing = math.sin((p - 2.6) / 0.9 * math.pi) * twist
    t.pose.bodyAngle = 3 * swing
    t.pose.chestAngle = 2 * swing
    t.pose.rootX = 2 * swing
    t.pose.blink = 1.0 if reach > 0.5 else blink(now)
    t.pose.cheek = 0.4 * reach
    t.pose.smile = 0.5 * twist + 0.3 * up
    t.pose.mouthOpen = 0.3 * abs(math.sin(a * 13)) if a < 1.6 else 0.15 * reach
    t.pose.tailAngle = 7 * wave(now, 0.8) * up
    return t


def reminding(a, now):
    """提醒：蹲一下 → 蹦起来 → 挥手说话 → 关切地看着你。"""
    t = Targets()
    crouch = bump(a, 0, 0.15, 0.05, 0.12)
    t.pose.bodyLift = -3 * crouch
    t.squash = -0.05 * crouch
    t.pose.rootY = 11.0 if (a > 0.2 and a < 0.42) else 0.0
    wave1 = bump(a, 0.3, 0.35, 1.9, 0.5)
    t.pose.rightArm = -58 * wave1 - 4
    t.pose.rightForearm = (-34 + 12 * wave(a, 0.55)) * wave1
    t.pose.leftArm = 6 * wave1 + 3
    t.pose.headAngle = -1.8 + 0.8 * wave(now, 3.3)
    t.pose.chestLift = 1.6 + 0.4 * wave(now, 3.2)
    t.pose.shoulderLift = 0.6
    talking = abs(math.sin(a * 13)) if a < 2.2 else 0.0
    t.pose.mouthOpen = 0.35 * talking
    t.pose.cheek = 0.2
    t.pose.tailAngle = 6 * wave(now, 0.9) * bump(a, 0.3, 0.3, 1.5, 0.6)
    t.pose.blink = blink(now)
    return t


def rewarding(a, now, happy):
    """happy：0 = 还没到收尾，1 = GIF 播完，笑着摇尾巴。由画布按 GIF 长度给。"""
    t = Targets()
    crouch = bump(a, 0, 0.08, 0.02, 0.06)
    reach = bump(a, 0.08, 0.15, 0.12, 0.2)
    t.pose.bodyLift = -3.5 * crouch + 0.8 * reach
    t.squash = -0.06 * crouch + 0.04 * reach
    t.pose.rootY = 9.0 if (a > 0.1 and a < 0.25) else 0.0
    # 伸完回到原画的姿势：GIF 里的 idle 就是原画，淡入淡出时一点不差。
    t.pose.leftArm = 82 * reach
    t.pose.rightArm = -85 * reach
    t.pose.leftForearm = 18 * reach
    t.pose.rightForearm = -19 * reach
    t.pose.chestLift = 2.6 * reach
    t.pose.shoulderLift = 1.6 * reach
    t.pose.chestAngle = -1.5 * reach
    t.pose.headAngle = -2.2 * reach
    t.pose.blink = 1.0 if reach > 0.5 else 0.0
    t.pose.cheek = 0.5 * reach
    t.pose.mouthOpen = 0.15 * reach
    # GIF 之后：笑着、尾巴摇得飞快。
    after = float(happy)
    t.pose.smile = 0.4 * reach + after * 0.9
    t.pose.tailAngle = 9 * wave(now, 0.45) * (reach + after)
    t.pose.chestLift += after * (1.2 + 0.5 * wave(now, 1.4))
    t.pose.bodyLift += after * 0.6 * abs(wave(now, 0.9))
    if after > 0:
        t.pose.blink = blink(now)
    return t
