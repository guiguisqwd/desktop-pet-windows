"""照翻 Pet2/Tools/preview.swift 的 --postures：每种姿势各演 7 秒，在第 3.5 秒和第 6.6 秒各存一张 PNG，检查动作用。
用法：python tools/preview.py <素材目录> <输出目录> [--postures | --reward 种类]"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt                                   # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter     # noqa: E402

from pet2win.core.brain import (Away, Brain, Calm, Drifting, Lines, Reminder, ReminderKind, Reminding, Reward,  # noqa: E402
                                Rewarding, Snoozed, Waiting)
from pet2win.core.posture_input import Issue, Side                      # noqa: E402
from pet2win.core.rewards import RewardKind, RewardRules                 # noqa: E402
from pet2win.app import pet_canvas as PC                                # noqa: E402

SCALE = 2.0


def render(st, out_path, background=0.93):
    img = QImage(int(PC.WIDTH * SCALE), int(PC.HEIGHT * SCALE), QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(SCALE)
    img.fill(QColor.fromRgbF(background, background, background, 1))
    p = QPainter(img)
    PC.paint(p, st, dpr=SCALE)
    p.end()
    img.save(out_path)


def postures(assets, out):
    reminder = Reminder(id=1, kind=ReminderKind.POSTURE, issue=Issue.HEAD_DOWN, side=None,
                        line=Lines.reminder(Issue.HEAD_DOWN, None, 180), started_at=0)
    reward = Reward(id=2, issue=Issue.HEAD_DOWN, line=Lines.reward(Issue.HEAD_DOWN), started_at=0)
    upright = Reminder(id=3, kind=ReminderKind.UPRIGHT, issue=None, side=None, line=Lines.upright, started_at=0)
    sitting = Reminder(id=4, kind=ReminderKind.SITTING, issue=None, side=None, line=Lines.sitting(45), started_at=0)
    lst = [
        ("00-平常坐直", Calm()),
        ("01-低头", Drifting(Issue.HEAD_DOWN, None, 0.8)),
        ("02-头前伸", Drifting(Issue.FORWARD_HEAD, None, 0.8)),
        ("03-耸肩", Drifting(Issue.SHRUG, None, 0.8)),
        ("04-歪头-用户往左", Drifting(Issue.HEAD_TILT, Side.LEFT, 0.8)),
        ("05-歪头-用户往右", Drifting(Issue.HEAD_TILT, Side.RIGHT, 0.8)),
        ("06-高低肩-左肩高", Drifting(Issue.UNEVEN_SHOULDERS, Side.LEFT, 0.8)),
        ("07-扭头-往左", Drifting(Issue.HEAD_TURN, Side.LEFT, 0.8)),
        ("08-弯腰", Drifting(Issue.SLOUCH, None, 0.8)),
        ("09-往后靠", Drifting(Issue.LEAN_BACK, None, 0.8)),
        ("10-滑坐", Drifting(Issue.SLIDE, None, 0.8)),
        ("11-提醒-低头", Reminding(reminder)),
        ("12-提醒-坐直太久", Reminding(upright)),
        ("13-提醒-久坐", Reminding(sitting)),
        ("14-改过来-爱心", Rewarding(reward)),
        ("15-稍后-钻地", Snoozed(time.time() + 540)),
        ("17-低头-犯困", Drifting(Issue.HEAD_DOWN, None, 0.6)),
        ("18-低头-睡着", Drifting(Issue.HEAD_DOWN, None, 0.95)),
        ("19-提醒后看到回正-嗯", Reminding(reminder)),
        ("16-没看到人", Away()),
    ]
    for name, scene in lst:
        st = PC.CanvasState(assets)
        st.gif_px_scale = SCALE
        if isinstance(scene, Drifting):
            st.drowsiness = scene.intensity
        if name.startswith("19-"):
            st.noticing = True
        marks = [1.6, 3.5] if name.startswith("12-") else ([3.5, 5.6] if name.startswith("15-") else [3.5, 6.6])
        t = 0.0
        while t < 7:
            st.step(scene, t, 1 / 30)
            for mark in marks:
                if abs(t - mark) < 0.5 / 30:
                    render(st, os.path.join(out, f"{name}-{'a' if mark == marks[0] else 'b'}.png"))
            t += 1 / 30
    print(f"姿势预览 → {out}")


def reward_story(assets, out, kind):
    st = PC.CanvasState(assets)
    st.gif_px_scale = SCALE
    issue = None if kind == RewardKind.CHIN_REST else Issue.HEAD_TILT
    today = 2 if kind == RewardKind.PLUS1 else 1
    one = Reward(id=3, issue=issue, line=RewardRules.line(kind, issue, today), started_at=0, kind=kind, today=today,
                 seconds=st.reward_seconds(kind))
    story = [(1, Calm()), (st.reward_seconds(kind), Rewarding(one)), (1.5, Calm())]
    t = 0.0
    frame = 0
    for length, scene in story:
        start = t
        while t < start + length:
            st.step(scene, t, 1 / 30)
            if frame % 15 == 0:
                render(st, os.path.join(out, f"t{t:05.1f}.png"))
            t += 1 / 30
            frame += 1
    print(f"奖励预览 → {out}")


def main():
    app = QGuiApplication(sys.argv[:1])   # noqa: F841
    assets, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    if len(sys.argv) > 4 and sys.argv[3] == "--reward":
        reward_story(assets, out, RewardKind(sys.argv[4]))
    else:
        postures(assets, out)


if __name__ == "__main__":
    main()
