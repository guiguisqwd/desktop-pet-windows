"""照翻 Pet2/Mac/PetCanvas.swift：桌宠唯一的一块画面：角色 + 头上的气泡 + 脚下一行小字。

只按 Brain 的 Scene 画，不做决定；用户点了什么通过回调交出去。
Mac 的画布 y 向上、原点在左下；Qt 是 y 向下、原点在左上。这里所有尺寸、位置都照抄 Mac 的数（Mac 坐标），
画的时候用 _q() 换成 Qt 坐标，这样和 Mac 版逐个数字对得上。
"""
import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter, QPainterPath, QPen,
                           QTextOption)

from pet2win.core.brain import Away, Calm, Drifting, Paused, ReminderKind, Reminding, Rewarding, Snoozed, Waiting
from pet2win.core.rewards import ALL_REWARD_KINDS, RewardKind
from pet2win.core.swiftcompat import swift_round_up

from . import animator as A
from .reward_show import RewardPlayer, RewardShow
from .whale_rig import WhaleRig

WIDTH, HEIGHT = 270.0, 390.0
GROUND_Y = 74.0
HAPPY_SECONDS = 2.0   # GIF 播完后开心摇尾巴多久
GIF_FADE = 0.25       # GIF 淡入、淡出各多久

CREAM = QColor.fromRgbF(0.995, 0.975, 0.92, 0.98)
INK = QColor.fromRgbF(0.27, 0.26, 0.24, 1)


def ui_font(px, weight=QFont.Normal):
    """Mac 的 systemFont(ofSize: px)：Windows 上用微软雅黑 UI，按逻辑像素给大小。"""
    f = QFont()
    f.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "sans-serif"])
    screen = QGuiApplication.primaryScreen()
    dpi = screen.logicalDotsPerInch() if screen is not None else 96.0
    f.setPointSizeF(px * 72.0 / dpi)
    f.setWeight(weight)
    return f


class CanvasState:
    """画面的状态和每帧的计算（PetCanvas.step），不管怎么画。"""

    def __init__(self, assets_dir):
        self.rig = WhaleRig(assets_dir)
        # 四套奖励 GIF：启动时只读时长和大小，真要播时才解码，播完放掉。
        self.shows = RewardShow.load_all(assets_dir)
        self.player = None
        self.animator = A.Animator()
        self.scene = Waiting("")
        self.clock = 0.0
        self.bubble_text = None
        self.bubble_hint = None
        self.bubble_shown_at = 0.0
        self.bubble_scale = A.Spring(0, frequency=3.2, damping=0.55)
        self.status_text = None
        self.overlay_status = None     # 宿主临时要说的一句（比如“正在导出…”），盖过平时脚下那行字
        self.drowsiness = None         # 当前不良姿势离提醒门槛走到哪了；None 就用场景自带的强度
        self.noticing = False          # 提醒后已经看到回正
        self.toast = None              # (文字, 到什么时候)
        self.reward_id = None
        self.reward_start = 0.0
        self.reported_reward = None
        self.bubble_rect = QRectF()    # Mac 坐标
        self.on_reward_presented = None
        self.gif_px_scale = 1.0        # GIF 解码时按屏幕缩放缩到多大
        self.act_changed_at = 0.0      # 上次换幕的时间（宿主用它决定帧率）
        self._last_act = None

    def say(self, text, seconds):
        self.toast = (text, self.clock + seconds)

    @property
    def heart_duration(self):
        s = self.shows.get(RewardKind.HEART)
        return s.seconds if s else 0.0

    def reward_lengths(self):
        return {k: self.reward_seconds(k) for k in ALL_REWARD_KINDS}

    def reward_seconds(self, kind):
        show = self._show(kind)
        return A.STRETCH_SECONDS + max(2.0, show.seconds if show else 0.0) + GIF_FADE + HAPPY_SECONDS

    def _show(self, kind):
        """这一种的素材缺了就退回爱心；爱心也缺，就只演伸懒腰和摇尾巴。"""
        return self.shows.get(kind) or self.shows.get(RewardKind.HEART)

    def step(self, scene, now, dt):
        """每帧一次。now 单调递增（秒）。"""
        self.scene = scene
        self.clock = now
        intensity = 0.0
        text = None
        hint = None
        self.status_text = None
        if isinstance(scene, Waiting):
            act = A.WAITING
            self.status_text = scene.text
        elif isinstance(scene, Away):
            act = A.AWAY
            self.status_text = "没看到人"
        elif isinstance(scene, Calm):
            act = A.CALM
        elif isinstance(scene, Drifting):
            act = A.drifting_act(scene.issue, scene.side)
            intensity = self.drowsiness if self.drowsiness is not None else scene.intensity
        elif isinstance(scene, Reminding):
            r = scene.reminder
            if self.noticing and r.kind == ReminderKind.POSTURE:
                act = A.noticed_act(r.id)
                text = "嗯！"
            else:
                act = A.reminding_upright_act(r.id) if r.kind == ReminderKind.UPRIGHT else A.reminding_act(r.id)
                if not r.bubble_hidden:
                    text = r.line
                    hint = "点我：知道了 / 稍后"
        elif isinstance(scene, Rewarding):
            r = scene.reward
            act = A.rewarding_act(r.id)
            text = r.line
            if self.reward_id != r.id:
                self.reward_id = r.id
                self.reward_start = now
                show = self._show(r.kind)
                size = None
                if show is not None:
                    rect = show.rect(GROUND_Y, WIDTH)
                    size = QSize(max(1, round(rect.width() * self.gif_px_scale)), max(1, round(rect.height() * self.gif_px_scale)))
                self.player = RewardPlayer.make(show, size)   # 这时才解码这一套
            # GIF 淡出完才开心摇尾巴；GIF 播着、淡出时身子保持原画的姿势。
            gif_end = A.STRETCH_SECONDS + ((self.player.show.seconds + GIF_FADE) if self.player else 0.0)
            intensity = 1.0 if now - self.reward_start >= gif_end else 0.0
        elif isinstance(scene, Snoozed):
            act = A.SNOOZED
            minutes = max(1, int(swift_round_up((scene.until - time.time()) / 60)))
            self.status_text = f"安静中 · 还有 {minutes} 分钟"
        else:
            act = A.PAUSED
            self.status_text = "已暂停"
        if not isinstance(scene, Rewarding):
            self.reward_id = None
            self.player = None

        if self.toast is not None and now < self.toast[1]:
            text = self.toast[0]
            hint = None
        else:
            self.toast = None
        if text != self.bubble_text:
            if text is not None:
                self.bubble_shown_at = now
                self.bubble_scale.x = 0.6
                self.bubble_scale.v = 0.0
            self.bubble_text = text
            self.bubble_hint = hint
        self.bubble_hint = hint
        self.bubble_scale.step(0.0 if text is None else 1.0, min(0.05, dt))
        if act != self._last_act:
            self._last_act = act
            self.act_changed_at = now
        self.animator.step(act, intensity, now, dt)

        if self.reward_id is not None and self.reported_reward != self.reward_id and now - self.reward_start >= A.STRETCH_SECONDS:
            self.reported_reward = self.reward_id
            if self.on_reward_presented:
                self.on_reward_presented(self.reward_id, self.player is not None)

    def interactive_rect(self):
        """能点到的地方（Mac 坐标）：角色、气泡。其余透明处不挡后面的窗口。"""
        rect = QRectF(40, GROUND_Y - 10, 190, 215)
        if self.bubble_text is not None and not self.bubble_rect.isEmpty():
            rect = rect.united(self.bubble_rect)
        return rect

    def heart_mix(self):
        """奖励 GIF 盖住身子的程度：开始前 0.25 秒淡入，播完 0.25 秒淡出。"""
        if self.reward_id is None or self.player is None:
            return 0.0
        length = self.player.show.seconds
        age = self.clock - self.reward_start - A.STRETCH_SECONDS
        fade = GIF_FADE
        if age < -fade or age > length + fade:
            return 0.0
        if age < 0:
            return (age + fade) / fade
        if age > length:
            return 1 - (age - length) / fade
        return 1.0


def _q(rect):
    """Mac 坐标的框（y 向上）→ Qt 坐标的框（y 向下）。"""
    return QRectF(rect.x(), HEIGHT - rect.y() - rect.height(), rect.width(), rect.height())


def _qy(y):
    return HEIGHT - y


def paint(p, st, dpr=1.0, layer_cache=None):
    """把整块画面画到 p 上（p 的坐标就是 270×390 的画布，y 向下）。"""
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    pose = st.animator.pose
    heart_mix = st.heart_mix()

    # 钻地时地上的洞：越往下越明显。
    burrow = min(1.0, max(0.0, -pose.rootY / 30))
    if burrow > 0:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor.fromRgbF(0.33, 0.25, 0.2, 0.55 * burrow))
        p.drawEllipse(_q(QRectF(135 - 62 + pose.rootX, GROUND_Y - 9, 124, 18)))
        pen = QPen(QColor.fromRgbF(0.55, 0.43, 0.33, 0.6 * burrow))
        pen.setWidthF(2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(_q(QRectF(135 - 64 + pose.rootX, GROUND_Y - 10, 128, 20)))
    # 地上的影子：跳起来变小变淡。
    lift = max(0.0, pose.rootY)
    shadow_w = 118 - lift * 2.2
    alpha = 0.16 * (1 - lift / 30) * pose.opacity * (1 - heart_mix) * (1 - burrow)
    if shadow_w > 0 and alpha > 0:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor.fromRgbF(0.1, 0.1, 0.1, min(1.0, alpha)))
        p.drawEllipse(_q(QRectF(135 - shadow_w / 2 + pose.rootX, GROUND_Y - 5, shadow_w, 11)))

    if heart_mix < 1:
        solid = pose.copy()
        solid.opacity = 1.0
        if pose.opacity >= 0.999:
            # 不透明时直接画（source-over 可以结合：先画进一层再贴上来，和直接画一模一样），省一张图的来回。
            p.save()
            _draw_character(p, st, solid, dpr)
            p.restore()
            layer = layer_cache
        else:
            # 半透明：整个角色先画进一层再整体变淡，不然各块重叠的地方会露出接缝。
            w, h = int(math.ceil(WIDTH * dpr)), int(math.ceil(HEIGHT * dpr))
            layer = layer_cache if (layer_cache is not None and layer_cache.width() == w and layer_cache.height() == h) else None
            if layer is None:
                layer = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            layer.setDevicePixelRatio(dpr)
            layer.fill(Qt.transparent)
            lp = QPainter(layer)
            _draw_character(lp, st, solid, dpr)
            lp.end()
            p.save()
            p.setOpacity(pose.opacity)
            p.drawImage(QRectF(0, 0, WIDTH, HEIGHT), layer)
            p.restore()
    else:
        layer = layer_cache
    if heart_mix > 0 and st.player is not None:
        # 淡入、淡出时停在第一帧 / 最后一帧（都是 idle），和身后的 WhaleRig 重合。
        st.player.draw(p, st.clock - st.reward_start - A.STRETCH_SECONDS, _q(st.player.show.rect(GROUND_Y, WIDTH)), heart_mix)
    if st.animator.doze > 0.01:
        _draw_doze(p, st, st.animator.doze)
    _draw_bubble(p, st)
    text = st.overlay_status or st.status_text
    if text:
        _draw_status(p, text)
    return layer


def _draw_character(lp, st, solid, dpr):
    lp.setRenderHint(QPainter.Antialiasing, True)
    lp.setRenderHint(QPainter.SmoothPixmapTransform, True)
    # 压扁拉长：以脚底中间为中心，宽度反向变化，体积看起来不变。
    sy = 1 + st.animator.squash
    sx = 1 / math.sqrt(sy)
    lp.translate(135, _qy(GROUND_Y))
    lp.scale(sx, sy)
    lp.translate(-135, -_qy(GROUND_Y))
    lp.translate(135 - 128.5, 0)   # 角色原画的中线在 x≈128.5
    if solid.rootY < -0.5 or st.animator.pose.rootY < -0.5:
        # 钻进地里：地面以下的部分不画。
        lp.setClipRect(_q(QRectF(-100, GROUND_Y - 1, WIDTH + 200, HEIGHT)))
    st.rig.draw(lp, solid, GROUND_Y, HEIGHT, dpr)


def bubble_layout(text, hint):
    """气泡的大小（和 Mac 一样：宽 244，字在 216 宽里换行，下面一行提示 17 高）。返回 (字的高度, 框高)。"""
    font = ui_font(13.5, QFont.Medium)
    fm = QFontMetricsF(font)
    br = fm.boundingRect(QRectF(0, 0, 244 - 28, 200), int(Qt.TextWordWrap | Qt.AlignLeft), text)
    text_h = math.ceil(br.height())
    hint_h = 17 if hint else 0
    return text_h, text_h + 22 + hint_h


def _draw_bubble(p, st):
    scale = st.bubble_scale.x
    text = st.bubble_text
    if text is None or scale <= 0.02:
        st.bubble_rect = QRectF()
        return
    width = 244.0
    text_h, height = bubble_layout(text, st.bubble_hint)
    head_top = GROUND_Y + 196 + max(0.0, st.animator.pose.rootY) * 0.6
    rect = QRectF((WIDTH - width) / 2, min(HEIGHT - height - 4, head_top + 14), width, height)   # Mac 坐标
    st.bubble_rect = rect
    q = _q(rect)

    p.save()
    # 从尾巴尖“弹”出来。
    anchor = QPointF(150, _qy(rect.y()))
    p.translate(anchor)
    p.scale(scale, scale)
    p.translate(-anchor)
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    path.addRoundedRect(q, 16, 16)
    base = q.bottom()
    tail = QPainterPath()
    tail.moveTo(138, base - 1)
    tail.cubicTo(144, base + 2, 148, base + 6, 150, base + 10)
    tail.cubicTo(153, base + 5, 156, base + 1, 160, base - 1)
    tail.closeSubpath()
    path.addPath(tail)
    # 影子：Mac 是 7 的模糊、往下 2；这里用几圈淡的描边近似。
    p.setBrush(Qt.NoBrush)
    for i, a in enumerate((0.05, 0.035, 0.025, 0.015)):
        pen = QPen(QColor.fromRgbF(0.2, 0.15, 0.1, a))
        pen.setWidthF(2 + i * 3)
        p.setPen(pen)
        p.drawPath(path.translated(0, 2))
    p.setPen(Qt.NoPen)
    p.setBrush(CREAM)
    p.drawPath(path)
    alpha = min(1.0, max(0.0, (scale - 0.5) * 2))
    ink = QColor(INK)
    ink.setAlphaF(alpha)
    p.setPen(ink)
    p.setFont(ui_font(13.5, QFont.Medium))
    p.drawText(QRectF(q.left() + 14, q.top() + 11, width - 28, text_h + 4), int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop), text)
    if st.bubble_hint:
        p.setFont(ui_font(10.5))
        p.setPen(QColor.fromRgbF(0.55, 0.5, 0.44, alpha))
        p.drawText(QRectF(q.left() + 14, q.bottom() - 8 - 14, width - 28, 14), int(Qt.AlignLeft | Qt.AlignBottom), st.bubble_hint)
    p.restore()


def _draw_status(p, text):
    font = ui_font(11, QFont.Medium)
    fm = QFontMetricsF(font)
    tw = fm.horizontalAdvance(text)
    pill = QRectF((WIDTH - tw) / 2 - 10, GROUND_Y - 34, tw + 20, 20)   # Mac 坐标
    q = _q(pill)
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor.fromRgbF(1, 1, 1, 0.82))
    p.drawRoundedRect(q, 10, 10)
    p.setFont(font)
    p.setPen(QColor.fromRgbF(0.42, 0.45, 0.5, 1))
    p.drawText(q, int(Qt.AlignCenter), text)
    p.restore()


def _draw_doze(p, st, alpha):
    """打瞌睡 / 睡着时头顶冒出来的 z。"""
    p.save()
    for k in range(3):
        phase = math.fmod(st.clock * 0.55 + k / 3, 1)
        x = 188 + phase * 22 + k * 2
        y = GROUND_Y + 180 + phase * 34
        a = alpha * math.sin(phase * math.pi)
        font = ui_font(11 + phase * 7, QFont.DemiBold)
        p.setFont(font)
        p.setPen(QColor.fromRgbF(0.45, 0.55, 0.75, max(0.0, min(1.0, a))))
        fm = QFontMetricsF(font)
        # Mac 的 draw(at:) 以字框左下角为准
        p.drawText(QPointF(x, _qy(y) - fm.descent()), "z")
    p.restore()
