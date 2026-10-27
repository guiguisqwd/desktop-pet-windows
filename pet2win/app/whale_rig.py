"""照翻 Pet2/Mac/WhaleRig.swift：一张原画切块摆姿势。头和眼睛不缩放，动作不换整张贴图。

坐标都按 1254 px 原画、y 向下；只在最后乘 202/1254 换到屏幕。Mac 版用 Core Graphics，这里用 QPainter：
- CG 的“translate(0,size); scale(1,-1); draw(image)”是在 y 向上的坐标里把图摆正，QPainter 本来就是 y 向下，直接 drawImage。
- CG 的 clip 用非零环绕规则，QPainterPath 默认奇偶规则，所以路径都设成 WindingFill。
- 原画 1254 px，屏幕上只画 200 来 px：先用高质量缩小成两倍屏幕大小再画，不然双线性缩六倍会有锯齿。
"""
import math
from collections import OrderedDict

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform

from pet2win.core.swiftcompat import swift_rounded

SIZE = 1254.0
SCALE = 202.0 / 1254.0
SHOE_BOTTOM = 1219.0
# 网格合成图只画这两块（原画坐标）：网格本身的范围再留出脖子转动、胸口抬起时的余量。
TORSO_REGION = QRectF(200, 590, 820, 540)
COLLAR_REGION = QRectF(440, 620, 380, 240)


def _polygon(points):
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    if not points:
        return path
    path.moveTo(points[0][0], points[0][1])
    for x, y in points[1:]:
        path.lineTo(x, y)
    path.closeSubpath()
    return path


def _rotated(point, angle, pivot):
    r = angle * math.pi / 180
    x, y = point[0] - pivot[0], point[1] - pivot[1]
    return (pivot[0] + x * math.cos(r) - y * math.sin(r), pivot[1] + x * math.sin(r) + y * math.cos(r))


def _smooth(value):
    t = min(1.0, max(0.0, value))
    return t * t * (3 - 2 * t)


def _rotate(p, degrees, point):
    p.translate(point[0], point[1])
    p.rotate(degrees)
    p.translate(-point[0], -point[1])


BODY = [(568, 697), (698, 697), (740, 713), (735, 795), (713, 817), (759, 884), (853, 977), (876, 1013), (804, 1057),
        (632, 1069), (473, 1059), (389, 1010), (400, 974), (484, 889), (550, 820), (524, 789), (518, 717)]
TAIL = [(830, 984), (857, 975), (886, 960), (914, 935), (936, 910), (923, 890), (916, 867), (918, 843), (928, 822), (940, 811),
        (953, 820), (972, 841), (989, 867), (1017, 866), (1047, 862), (1078, 854), (1093, 857), (1098, 874), (1093, 897),
        (1083, 916), (1067, 933), (1047, 943), (1018, 948), (986, 944), (978, 971), (963, 996), (940, 1019), (912, 1033),
        (880, 1043), (849, 1040), (824, 1030)]
LEFT_ARM = [(522, 734), (536, 763), (551, 793), (546, 810), (532, 824), (518, 844), (502, 861), (495, 875), (478, 886), (456, 904),
            (437, 921), (418, 935), (406, 940), (396, 930), (385, 932), (374, 924), (373, 913), (362, 908), (361, 894), (372, 883),
            (395, 874), (416, 865), (432, 849), (428, 836), (433, 825), (448, 816), (444, 798), (446, 782), (458, 769), (478, 757),
            (501, 745)]
RIGHT_ARM = [(729, 734), (746, 746), (771, 759), (790, 772), (801, 789), (800, 804), (788, 819), (805, 824), (818, 837), (819, 849),
             (832, 863), (852, 875), (870, 882), (884, 893), (888, 905), (880, 915), (880, 925), (868, 932), (856, 929), (850, 937),
             (836, 939), (823, 930), (805, 916), (786, 899), (770, 882), (758, 871), (748, 856), (737, 843), (730, 826), (718, 815),
             (718, 793), (722, 768)]


class WhaleRig:
    def __init__(self, assets_dir):
        import os
        self._full = {}
        for name in ("idle", "blink", "rig-clean-v5"):
            img = QImage(os.path.join(assets_dir, f"{name}.png"))
            if not img.isNull():
                self._full[name] = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self._textures = {}
        self._texture_px = None
        torso_mask = QPainterPath()
        torso_mask.setFillRule(Qt.WindingFill)
        torso_mask.addPath(_polygon(BODY))
        torso_mask.addRect(QRectF(450, 697, 400, 277))
        torso_mask.addPath(_polygon([(290, 697), (522, 697), (523, 810), (460, 895), (440, 943), (290, 943)]))
        torso_mask.addPath(_polygon([(735, 697), (930, 697), (930, 943), (812, 943), (730, 824)]))
        self._torso_layer = self._layer(torso_mask)
        self._collar_layer = self._layer(_polygon([(523, 704), (559, 701), (697, 701), (736, 708), (735, 756), (710, 800),
                                                   (557, 800), (525, 756)]))
        self._cached_torso = None
        self._cached_collar = None
        self._cached_geometry = None
        self._raster = 512
        self._cache = OrderedDict()   # 网格合成图的缓存：呼吸是周期性的，同一组取整后的数会反复出现
        self._left_foot = _polygon([(460, 1035), (633, 1035), (633, 1254), (460, 1254)])
        self._right_foot = _polygon([(633, 1035), (800, 1035), (800, 1254), (633, 1254)])
        self._tail = _polygon(TAIL)
        self._head = QPainterPath()
        self._head.setFillRule(Qt.WindingFill)
        self._head.addRect(QRectF(0, 0, SIZE, 701))
        self._eyes = QPainterPath()
        self._eyes.setFillRule(Qt.WindingFill)
        self._eyes.addRoundedRect(QRectF(463, 494, 143, 141), 10, 10)
        self._eyes.addRoundedRect(QRectF(681, 499, 141, 136), 10, 10)
        self._arms = {}
        for left, pts in ((True, LEFT_ARM), (False, RIGHT_ARM)):
            arm = _polygon(pts)
            upper = QPainterPath()
            upper.addRect(QRectF(0, 0, SIZE, 841))
            lower = QPainterPath()
            lower.addRect(QRectF(0, 831, SIZE, SIZE - 831))
            self._arms[left] = (arm.intersected(upper), arm.intersected(lower))

    @property
    def loaded(self):
        return "idle" in self._full

    # ---------- 贴图 ----------

    def _ensure_textures(self, dpr):
        """按屏幕的缩放准备缩小好的贴图（两倍屏幕大小）。"""
        px = int(math.ceil(SIZE * SCALE * max(1.0, dpr) * 2))
        px = max(256, min(int(SIZE), px))
        if px == self._texture_px:
            return
        self._texture_px = px
        self._textures = {name: img.scaled(px, px, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                          for name, img in self._full.items()}
        self._raster = max(256, min(1024, int(math.ceil(SIZE * SCALE * max(1.0, dpr) * 2.5))))
        self._torso_small = self._torso_layer.scaled(self._raster, self._raster, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) \
            if self._torso_layer is not None else None
        self._collar_small = self._collar_layer.scaled(self._raster, self._raster, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) \
            if self._collar_layer is not None else None
        self._cached_geometry = None
        self._cache.clear()

    def _piece(self, p, name, path, textures=None):
        tex = (textures or self._textures).get(name) or (textures or self._textures).get("idle")
        if tex is None:
            return
        p.save()
        p.setClipPath(path, Qt.IntersectClip)
        p.drawImage(QRectF(0, 0, SIZE, SIZE), tex)
        p.restore()

    def _layer(self, mask):
        src = self._full.get("rig-clean-v5") or self._full.get("idle")
        if src is None:
            return None
        img = QImage(int(SIZE), int(SIZE), QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # 头后面藏着的四个像素：盖住头上两条小数覆盖的边，不改头露出来的部分。
        p.setClipRect(QRectF(0, 697, SIZE, SIZE - 697))
        p.setClipPath(mask, Qt.IntersectClip)
        p.drawImage(QRectF(0, 0, SIZE, SIZE), src)
        p.end()
        return img

    # ---------- 画 ----------

    def draw(self, p, pose, ground_y, canvas_height, dpr=1.0):
        """p 是 y 向下的 QPainter（桌宠画布的坐标）。canvas_height 用来把 Mac 的 y 向上换过来。"""
        if not self._full:
            return
        self._ensure_textures(dpr)
        self._prepare_torso(pose)
        left = self._arm_placement(True, pose.leftArm, pose.leftForearm, pose)
        right = self._arm_placement(False, pose.rightArm, pose.rightForearm, pose)
        p.save()
        p.setOpacity(pose.opacity)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # Mac：translate(26.5 + rootX, groundY + shoeBottom·scale + rootY) 再 scale(scale, -scale)（y 向上的画布）
        p.translate(26.5 + pose.rootX, canvas_height - (ground_y + SHOE_BOTTOM * SCALE + pose.rootY))
        p.scale(SCALE, SCALE)

        # 脚不跟着腰和头歪，压在裙摆下面。
        p.save()
        p.translate(0, -pose.leftFootLift / SCALE)
        self._piece(p, "idle", self._left_foot)
        p.restore()
        p.save()
        p.translate(0, -pose.rightFootLift / SCALE)
        self._piece(p, "idle", self._right_foot)
        p.restore()

        p.save()
        p.translate(0, -pose.bodyLift / SCALE)
        _rotate(p, pose.bodyAngle, (637, 1042))

        p.save()
        _rotate(p, pose.tailAngle, (848, 1004))
        self._piece(p, "idle", self._tail)
        p.restore()

        self._draw_cached(p, self._cached_torso, TORSO_REGION)

        p.save()
        p.translate(0, -pose.chestLift / SCALE)
        _rotate(p, pose.chestAngle, (637, 902))
        _rotate(p, pose.headAngle, (637, 697))
        self._piece(p, "idle", self._head)
        if pose.blink > 0.001:
            p.save()
            p.setOpacity(pose.opacity * min(1.0, pose.blink))
            self._piece(p, "blink", self._eyes)
            p.restore()
        self._draw_expression(p, pose)
        p.restore()

        self._draw_arm(p, True, left)
        self._draw_arm(p, False, right)
        self._draw_cached(p, self._cached_collar, COLLAR_REGION)
        p.restore()
        p.restore()

    def _skinned(self, point, p):
        """衣服和后面的头发跟着弯；脖子处权重正好是 1，和不缩放的头接上。"""
        x, y = point
        neck_weight = _smooth((805 - y) / 104)
        chest_weight = _smooth((1050 - y) / 245)
        neck = _rotated(point, p.headAngle, (637, 697))
        vx = x + (neck[0] - x) * neck_weight
        vy = y + (neck[1] - y) * neck_weight
        shoulder_weight = _smooth((y - 701) / 42) * (1 - _smooth((y - 760) / 105))
        vy -= p.shoulderLift / SCALE * shoulder_weight
        chest = _rotated((vx, vy), p.chestAngle, (637, 902))
        vx += (chest[0] - vx) * chest_weight
        vy += (chest[1] - vy) * chest_weight - p.chestLift / SCALE * chest_weight
        return (vx, vy)

    def _prepare_torso(self, pose):
        # 只缓存会弯的那两层。Mac 按 0.025° / 0.01pt 取整；Python 每次重画网格要一毫秒左右，这里放宽到 0.2° / 0.12pt
        # （脖子接缝处最多差 0.06pt，屏幕上看不出来），呼吸时每秒只重画几次，平常坐着时 CPU 占用降到几个百分点。
        def rounded(value, step):
            return swift_rounded(value / step) * step
        key = (rounded(pose.headAngle, 0.2), rounded(pose.chestAngle, 0.2),
               rounded(pose.chestLift, 0.12), rounded(pose.shoulderLift, 0.12))
        if key == self._cached_geometry:
            return
        hit = self._cache.get(key)
        if hit is None:
            geometry = pose.copy()
            geometry.headAngle, geometry.chestAngle, geometry.chestLift, geometry.shoulderLift = key
            hit = (self._rasterized_torso(self._torso_small, geometry, False, TORSO_REGION),
                   self._rasterized_torso(self._collar_small, geometry, True, COLLAR_REGION))
            self._cache[key] = hit
            while len(self._cache) > 48:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        self._cached_torso, self._cached_collar = hit
        self._cached_geometry = key

    def _rasterized_torso(self, texture, pose, front, region):
        """把网格合成一张图，免得三角形之间露出缝。只画 region 那一块（原画坐标），省内存也省时间。"""
        if texture is None:
            return None
        k = self._raster / SIZE
        img = QImage(max(1, int(math.ceil(region.width() * k))), max(1, int(math.ceil(region.height() * k))),
                     QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.scale(k, k)
        p.translate(-region.left(), -region.top())
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self._draw_torso_mesh(p, texture, pose, front)
        p.end()
        return img

    def _draw_cached(self, p, texture, region):
        if texture is not None:
            p.drawImage(QRectF(region.left(), region.top(), texture.width() * SIZE / self._raster,
                               texture.height() * SIZE / self._raster), texture)

    def _draw_torso_mesh(self, p, texture, pose, front):
        xs = [515, 637, 744] if front else [288, 637, 932]
        ys = [701, 718, 740, 765, 785, 802] if front else [697, 718, 740, 765, 790, 820, 850, 885, 920, 955, 990, 1030, 1072]
        for row in range(len(ys) - 1):
            for column in range(len(xs) - 1):
                a = (xs[column], ys[row])
                b = (xs[column + 1], ys[row])
                d = (xs[column], ys[row + 1])
                e = (xs[column + 1], ys[row + 1])
                aa, bb = self._skinned(a, pose), self._skinned(b, pose)
                dd, ee = self._skinned(d, pose), self._skinned(e, pose)
                self._triangle(p, texture, [a, b, d], [aa, bb, dd])
                self._triangle(p, texture, [b, e, d], [bb, ee, dd])

    def _triangle(self, p, texture, s, d):
        sx1, sy1 = s[1][0] - s[0][0], s[1][1] - s[0][1]
        sx2, sy2 = s[2][0] - s[0][0], s[2][1] - s[0][1]
        dx1, dy1 = d[1][0] - d[0][0], d[1][1] - d[0][1]
        dx2, dy2 = d[2][0] - d[0][0], d[2][1] - d[0][1]
        det = sx1 * sy2 - sx2 * sy1
        a = (dx1 * sy2 - dx2 * sy1) / det
        b = (dy1 * sy2 - dy2 * sy1) / det
        c = (dx2 * sx1 - dx1 * sx2) / det
        dd = (dy2 * sx1 - dy1 * sx2) / det
        transform = QTransform(a, b, c, dd, d[0][0] - a * s[0][0] - c * s[0][1], d[0][1] - b * s[0][0] - dd * s[0][1])
        p.save()
        # 相邻的三角形要严丝合缝：每个三角形往外多盖 2 个单位（Mac 版是三角形加一圈 4 宽的描边），
        # 重叠的地方直接覆盖（Source），不叠加透明度，不然浅色桌面上会露出暗线。
        p.setClipPath(_expanded_triangle(d, 2.0), Qt.IntersectClip)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.setWorldTransform(transform, True)
        p.drawImage(QRectF(0, 0, SIZE, SIZE), texture)
        p.restore()

    def _draw_expression(self, p, pose):
        open_ = min(1.0, max(0.0, pose.mouthOpen))
        smile = min(1.0, max(0.0, pose.smile))
        exhale = min(1.0, max(0.0, pose.exhale))
        amount = min(1.0, max(open_ * 4, smile * 3, exhale * 4))
        if pose.cheek > 0.001:
            p.save()
            p.setOpacity(pose.opacity * min(1.0, pose.cheek) * 0.12)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor.fromRgbF(1, 0.42, 0.36, 1))
            p.drawEllipse(QRectF(514, 621, 42, 21))
            p.drawEllipse(QRectF(743, 623, 42, 21))
            p.restore()
        if amount <= 0.001:
            return
        p.save()
        p.setOpacity(pose.opacity * amount)
        # 取旁边原画的皮肤盖住原来的小嘴，保留原画的渐变，不贴一块平涂的颜色。
        p.save()
        skin = QPainterPath()
        skin.addEllipse(QRectF(623, 616, 47, 47))
        p.setClipPath(skin, Qt.IntersectClip)
        p.translate(0, -22)
        rect_path = QPainterPath()
        rect_path.addRect(QRectF(619, 634, 56, 55))
        self._piece(p, "idle", rect_path)
        p.restore()
        mouth = QColor.fromRgbF(0.47, 0.25, 0.22, 1)
        if open_ + exhale > 0.04:
            width = 10 + open_ * 23 + exhale * 4
            height = 5 + open_ * 27 + exhale * 12
            rect = QRectF(646 - width / 2, 632 - height * 0.25, width, height)
            p.setPen(Qt.NoPen)
            p.setBrush(mouth)
            p.drawEllipse(rect)
            if open_ > 0.45:
                p.save()
                clip = QPainterPath()
                clip.addEllipse(rect)
                p.setClipPath(clip, Qt.IntersectClip)
                p.setBrush(QColor.fromRgbF(0.92, 0.55, 0.49, 1))
                p.drawEllipse(QRectF(rect.left() + 3, rect.center().y() + 3, rect.width() - 6, rect.height() * 0.65))
                p.restore()
        else:
            pen = QPen(mouth)
            pen.setWidthF(3.3)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            half = 5 + smile * 11
            path = QPainterPath()
            path.moveTo(646 - half, 632 - smile)
            path.quadTo(646, 633 + 15 * smile, 646 + half, 632 - smile)
            p.drawPath(path)
        p.restore()

    def _arm_placement(self, left, shoulder_angle, elbow_angle, pose):
        shoulder = (541, 752) if left else (718, 752)
        elbow = (474, 841) if left else (782, 841)
        grip = (396, 906) if left else (856, 908)
        socket = self._skinned(shoulder, pose)
        socket_side = self._skinned((shoulder[0] + 1, shoulder[1]), pose)
        socket_angle = math.atan2(socket_side[1] - socket[1], socket_side[0] - socket[0]) * 180 / math.pi
        upper = shoulder_angle + socket_angle
        forearm = elbow_angle
        hold = _smooth(pose.sign)
        if hold > 0:
            center = self._skinned((627, 870), pose)
            half_grip = (93 - 16) / (2 * SCALE)
            target = (center[0] + (-half_grip if left else half_grip), center[1] - pose.signLift / SCALE)
            first = math.hypot(elbow[0] - shoulder[0], elbow[1] - shoulder[1])
            second = math.hypot(grip[0] - elbow[0], grip[1] - elbow[1])
            dx, dy = target[0] - socket[0], target[1] - socket[1]
            distance = min(first + second - 0.01, max(abs(first - second) + 0.01, math.hypot(dx, dy)))
            heading = math.atan2(dy, dx)
            cosine = (first * first + distance * distance - second * second) / (2 * first * distance)
            bend = math.acos(min(1.0, max(-1.0, cosine)))
            first_heading = heading + (-bend if left else bend)
            solved_elbow = (socket[0] + first * math.cos(first_heading), socket[1] + first * math.sin(first_heading))
            reachable = (socket[0] + distance * math.cos(heading), socket[1] + distance * math.sin(heading))
            second_heading = math.atan2(reachable[1] - solved_elbow[1], reachable[0] - solved_elbow[0])
            original_first = math.atan2(elbow[1] - shoulder[1], elbow[0] - shoulder[0])
            original_second = math.atan2(grip[1] - elbow[1], grip[0] - elbow[0])
            solved_upper = (first_heading - original_first) * 180 / math.pi
            solved_forearm = (second_heading - original_second) * 180 / math.pi - solved_upper
            upper += _wrapped(solved_upper - upper) * hold
            forearm += _wrapped(solved_forearm - forearm) * hold
        return {"shoulder": shoulder, "elbow": elbow, "grip": grip, "socket": socket, "upper": upper, "forearm": forearm}

    def _draw_arm(self, p, left, arm):
        # Mac：先 clip 到 y < 841 再按手臂的形状画大臂；转过小臂以后 clip 到 y > 831 再画小臂。
        # 两个框和手臂的形状在同一个坐标里，所以开头就求好交集（self._arms），每帧不用再在旋转后裁框。
        upper_path, lower_path = self._arms[left]
        p.save()
        p.translate(arm["socket"][0] - arm["shoulder"][0], arm["socket"][1] - arm["shoulder"][1])
        _rotate(p, arm["upper"], arm["shoulder"])
        self._piece(p, "idle", upper_path)
        _rotate(p, arm["forearm"], arm["elbow"])
        self._piece(p, "idle", lower_path)
        p.restore()


def _wrapped(value):
    angle = math.fmod(value, 360)
    if angle > 180:
        angle -= 360
    if angle < -180:
        angle += 360
    return angle


def _expanded_triangle(d, by):
    """三角形每条边往外推 by，两条相邻的边在角上相交（斜接）。和 Mac 版“三角形 ∪ 宽 2·by 的描边（斜接，上限 4）”一样。"""
    (x0, y0), (x1, y1), (x2, y2) = d
    area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    sign = 1.0 if area > 0 else -1.0
    pts = [(x0, y0), (x1, y1), (x2, y2)]
    lines = []
    for i in range(3):
        ax, ay = pts[i]
        bx, by_ = pts[(i + 1) % 3]
        ex, ey = bx - ax, by_ - ay
        length = math.hypot(ex, ey) or 1.0
        # 往外的法线：三角形逆时针（area > 0）时是 (ey, -ex)
        nx, ny = sign * ey / length, -sign * ex / length
        lines.append(((ax + nx * by, ay + ny * by), (ex, ey)))
    out = []
    for i in range(3):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(cross) < 1e-9:
            out.append(p2)
            continue
        t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / cross
        out.append((p1[0] + d1[0] * t, p1[1] + d1[1] * t))
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in out]))
    path.closeSubpath()
    return path
