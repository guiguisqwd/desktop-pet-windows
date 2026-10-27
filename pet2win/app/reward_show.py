"""照翻 Pet2/Mac/RewardShow.swift + GIFClip.swift：一套奖励 GIF 怎么播、画在哪。

一套 = enter 播一次 → main 播一次或 loop 播几圈 → exit 播一次。enter 的第一帧和 exit 的最后一帧都是 idle，
画的框让 GIF 里的 idle 和 WhaleRig 画的 idle 完全重合，所以和代码动作淡入淡出时看不出跳。
- 启动时只读每帧时长和画布大小（自己解析 GIF 文件头，不解码）。
- 真要播的时候才解码（QImageReader），播完放掉：600×620 的帧解出来很占内存。
"""
import os
import struct

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter

from pet2win.core.rewards import ALL_REWARD_KINDS, RewardKind

# 每套的组成。loop 一圈很短的多播几圈，让每套 GIF 都在 3–5 秒。
RECIPES = {
    RewardKind.HEART: [("enter", 1), ("main", 1), ("exit", 1)],
    RewardKind.THUMBS_UP: [("enter", 1), ("loop", 2), ("exit", 1)],
    RewardKind.PLUS1: [("enter", 1), ("loop", 3), ("exit", 1)],
    RewardKind.CHIN_REST: [("enter", 1), ("loop", 2), ("exit", 1)],
}

# WhaleRig 把 idle.png 的像素 (X, Y) 画到画布上的 (rigX + X·σ, groundY + (1219 − Y)·σ)（Mac 的 y 向上），σ = 202/1254。
SIGMA = 202.0 / 1254.0
RIG_X = (135 - 128.5) + 26.5
SHOE_BOTTOM = 1219.0


def scan(path):
    """只读属性：每帧多长、画布多大。和 Mac 一样：帧时长 = GIF 里写的（1/100 秒），0 或没写按 0.1 秒；只有一帧不算。
    返回 (秒数, (宽, 高)) 或 None。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) < 13 or data[:3] != b"GIF":
        return None
    width, height, flags = struct.unpack("<HHB", data[6:11])
    pos = 13
    if flags & 0x80:
        pos += 3 * (2 ** ((flags & 0x07) + 1))
    total = 0.0
    frames = 0
    delay = None
    n = len(data)
    try:
        while pos < n:
            b = data[pos]
            if b == 0x3B:   # 结尾
                break
            if b == 0x21:   # 扩展块
                label = data[pos + 1]
                pos += 2
                if label == 0xF9 and data[pos] >= 4:   # 图形控制扩展：帧时长
                    delay = struct.unpack("<H", data[pos + 2:pos + 4])[0]
                while True:
                    size = data[pos]
                    pos += 1
                    if size == 0:
                        break
                    pos += size
            elif b == 0x2C:   # 一帧
                lflags = data[pos + 9]
                pos += 10
                if lflags & 0x80:
                    pos += 3 * (2 ** ((lflags & 0x07) + 1))
                pos += 1   # LZW 最小码长
                while True:
                    size = data[pos]
                    pos += 1
                    if size == 0:
                        break
                    pos += size
                raw = (delay / 100.0) if delay else 0.0
                total += raw if raw > 0 else 0.1
                frames += 1
                delay = None
            else:
                break
    except IndexError:
        pass
    if frames <= 1 or width <= 0 or height <= 0:
        return None
    return total, (float(width), float(height))


class Part:
    def __init__(self, path, plays, seconds):
        self.path, self.plays, self.seconds = path, plays, seconds


class RewardShow:
    def __init__(self, kind, parts, pixel_size):
        self.kind = kind
        self.parts = parts
        self.pixel_size = pixel_size   # GIF 画布的像素大小（一套里每个文件一样大）

    @property
    def seconds(self):
        """整套 GIF 播多久。"""
        return sum(p.seconds * p.plays for p in self.parts)

    @classmethod
    def load(cls, kind, assets):
        folder = os.path.join(assets, "whale", kind.value)
        parts = []
        size = None
        for name, plays in RECIPES.get(kind, []):
            path = os.path.join(folder, f"{kind.value}_{name}.gif")
            info = scan(path)
            if info is None or (size is not None and size != info[1]):
                return None
            size = info[1]
            parts.append(Part(path, plays, info[0]))
        if size is None or not parts:
            return None
        return cls(kind, parts, size)

    @classmethod
    def load_all(cls, assets):
        """启动时调一次。缺素材的那种不放进来（播的时候退回爱心）。"""
        shows = {}
        for kind in ALL_REWARD_KINDS:
            show = cls.load(kind, assets)
            if show is not None:
                shows[kind] = show
        # 老素材兜底：没有 whale/heart/ 时用原来的 whale-heart.gif。
        if RewardKind.HEART not in shows:
            path = os.path.join(assets, "whale-heart.gif")
            info = scan(path)
            if info is not None:
                shows[RewardKind.HEART] = cls(RewardKind.HEART, [Part(path, 1, info[0])], info[1])
        return shows

    def rect(self, ground_y, canvas_width):
        """这套 GIF 画在桌宠画布的哪个框里（Mac 坐标，y 向上），GIF 里的 idle 才和 WhaleRig 的 idle 重合。"""
        w, h = self.pixel_size
        if w == 320 and h == 300:
            # 320×300 的老规格：idle 缩成 314 px 宽，左上角贴在 (2, −10)。
            k, ox, oy = 314.0 / 1254, 2.0, -10.0
            s = SIGMA / k
            return QRectF(RIG_X - ox * s, ground_y + SHOE_BOTTOM * SIGMA - (h - oy) * s, w * s, h * s)
        # 动画项目的新规格（600×620 等）：宽 240pt、水平居中、底边在 groundY − 6，idle 就正好重合。
        width = 240.0
        return QRectF((canvas_width - width) / 2, ground_y - 6, width, width * h / w)


class GIFClip:
    """解码一次，用原来的每帧时长；frame_size 给了就缩到这么大（高质量缩小）。"""

    def __init__(self, path, frame_size=None):
        reader = QImageReader(path)
        reader.setAutoTransform(False)
        frames = []
        ends = []
        elapsed = 0.0
        delays = _delays(path)
        index = 0
        while True:
            img = reader.read()
            if img.isNull():
                break
            raw = delays[index] if index < len(delays) else 0
            seconds = raw / 100.0 if raw else 0.0
            elapsed += seconds if seconds > 0 else 0.1
            img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
            if frame_size is not None:
                img = img.scaled(frame_size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            frames.append(img)
            ends.append(elapsed)
            index += 1
        if len(frames) <= 1:
            raise ValueError(f"GIF 读不出来：{path}")
        self.frames = frames
        self.frame_ends = ends
        self.duration = elapsed

    def frame_index(self, age):
        if not (age == age) or age < 0 or age >= self.duration:
            return None
        for i, end in enumerate(self.frame_ends):
            if age < end:
                return i
        return None

    def draw(self, p, age, rect, fraction=1.0):
        i = self.frame_index(age)
        if i is None:
            return
        p.save()
        p.setOpacity(fraction)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(rect, self.frames[i])
        p.restore()


def _delays(path):
    """每帧写的时长（1/100 秒），和 scan 用同一套解析。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    out = []
    flags = data[10]
    pos = 13 + (3 * (2 ** ((flags & 0x07) + 1)) if flags & 0x80 else 0)
    delay = 0
    try:
        while pos < len(data):
            b = data[pos]
            if b == 0x3B:
                break
            if b == 0x21:
                label = data[pos + 1]
                pos += 2
                if label == 0xF9 and data[pos] >= 4:
                    delay = struct.unpack("<H", data[pos + 2:pos + 4])[0]
                while data[pos]:
                    pos += data[pos] + 1
                pos += 1
            elif b == 0x2C:
                lflags = data[pos + 9]
                pos += 10 + (3 * (2 ** ((lflags & 0x07) + 1)) if lflags & 0x80 else 0) + 1
                while data[pos]:
                    pos += data[pos] + 1
                pos += 1
                out.append(delay)
                delay = 0
            else:
                break
    except IndexError:
        pass
    return out


class RewardPlayer:
    """一次奖励真正要播的时候才解码这一套 GIF。frame_size：画到屏幕上的像素大小（按屏幕缩放算好）。"""

    def __init__(self, show, frame_size=None):
        self.show = show
        self.clips = [GIFClip(part.path, frame_size) for part in show.parts]

    @classmethod
    def make(cls, show, frame_size=None):
        if show is None:
            return None
        try:
            return cls(show, frame_size)
        except (ValueError, OSError):
            return None

    def draw(self, p, age, rect, fraction):
        """age：从 enter 第一帧开始的秒数。超出两头就停在第一帧 / 最后一帧（都是 idle）。"""
        t = min(max(0.0, age), self.show.seconds - 0.001)
        for index, clip in enumerate(self.clips):
            span = clip.duration * self.show.parts[index].plays
            if t < span or index == len(self.clips) - 1:
                local = min(t, span - 0.0005) % clip.duration if clip.duration > 0 else 0
                clip.draw(p, local, rect, fraction)
                return
            t -= span
