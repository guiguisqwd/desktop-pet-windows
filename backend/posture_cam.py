"""在 Mac 上用摄像头 + YOLO11n-pose（乐鑫 coco_pose 的原版模型）试跑体态检查。

六项检查，都和你自己校准时比：
  低头    眼睛和耳朵的夹角：低头时眼睛会往耳朵下面掉
  头前伸  头变大（往屏幕凑），或鼻子往两肩之间沉（鼻子到两肩的夹角张开）。
          低头时鼻子也会沉，但眼耳夹角也跟着变：眼耳夹角变了就算低头，不算伸头
  耸肩    左右两边肩膀都离耳朵变近，并且肩膀确实往上走了
          （只有一边变近是高低肩；头往下沉、椅子调高都不算）
  歪头    两眼连线歪了
  高低肩  左右两个肩膀点的高度差变了（肩线歪了）
  扭头    鼻子偏到两眼中点的一边
画面只在内存里处理，不保存任何图片。只写纯数字的文件：开了 --log 时的记录（log.csv，
和提醒之后有没有改过来的 log-alerts.csv），和自测结果 selftest.csv。

按键（预览窗口要在最前面）：
  c  校准：坐正，看屏幕，按一下，等它采完 5 次
  t  颈肩自测（约 3 分钟）：跟着窗口里的大字做动作，第二轮每个动作做完问两边疼几分、紧几分，
     结果存进 selftest.csv
  做动作前先按数字标记：1 头前伸  2 耸肩  3 歪头  4 扭头  5 低头  6 一边肩膀高  0 正常坐着
  q  退出
标记只写进 CSV，用来事后调阈值。

打包成 Mac 应用（双击打开、不用终端）时是“应用模式”：提示都在窗口里，窗口下面有按钮，
数据固定存在 ~/体态测试数据，用自带的 .onnx 模型（不用装 PyTorch）。桌宠从命令行启动它的参数见 README。
窗口里的“导出数据（E）”或者 --export 把数据打成一个 zip，好发给 gui：自测时打的疼痛、拉扯分数，
和 7 个点的坐标，都要用户单独同意才放进去（见 export_data）。
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from collections import deque

import cv2
import numpy as np
try:
    from ultralytics import YOLO
except ImportError:  # 打包成应用时不带 Ultralytics 和 PyTorch，用 .onnx 模型（见 OnnxPose）
    YOLO = None

# ---------- 判断算法 ----------
# 用“头变大”判头前伸、耸肩和肩线角度的基本算法，和 Dorso 补丁、乐鑫 C++ 版（upper_body.hpp）一样；
# 其余几项目前只在这个脚本里，调好以后再搬到板子上。
NOSE, L_EYE, R_EYE, L_EAR, R_EAR, L_SH, R_SH = range(7)
MIN_SCORE = 0.3
MIN_SHOULDER_PX = 40
# 肩膀点离画面底边不到这个比例（按画面高度）就当看不见：gui 的摆法肩膀常卡在底边，模型会把肩膀“猜”在边上，
# 肩宽一乱，头前伸的“头变大”、耸肩、高低肩都不可信（10-27 弯腰被报成头前伸就是这样）
SHOULDER_EDGE = 0.03

# 用 gui 2026-10-26 第一轮录的数据调过一次（一个人、约 4 分半，在同一份数据上挑的），以后多录几轮再调。
# 那一轮的数（都是和校准比的变化）：
#   鼻肩夹角：正常坐着 +3~+17°，故意伸头 +27~+30°
#   头变大：正常 −5%~+5%，故意伸头 +8%~+12%
#   眼耳：正常 −0.02~−0.07，伸头 −0.09~−0.11，低头 −0.18~−0.23
#   肩上抬：正常 −6%~+3%，耸肩 +7%~+8%
#   肩线：正常最多 1.7°，故意抬一边肩 1.4~4.7°
TH = dict(
    # 头前伸：头（两耳间距）相对肩宽变大多少
    forward_head_growth=0.08,
    # 头前伸的另一个办法：从鼻子往两肩各连一条线，这个夹角比校准时张开几度（鼻子往肩膀沉）。
    # 原来 6°，但你平时坐着就比校准时大约 10°（校准时坐得比平时正），所以改成 20°
    forward_head_nose_deg=20.0,
    # 耸肩：肩膀上抬超过肩宽的这个比例，并且两边耳朵到肩膀的距离都短了这么多。
    # 耳到肩的距离 10-27 起按“两耳间距”算比例，不再按当时的肩宽：gui p6 耸肩时两个肩膀点往里收、肩宽窄了 4.6%，
    # 按肩宽算只变近 0.149，正好卡在 0.15 下面；按耳距算是 0.17~0.21（旧校准没存耳距的，还按肩宽算）
    shrug_rise=0.05, shrug_neck_drop=0.15,
    # 歪头：两眼连线歪了几度；高低肩：肩线歪了几度（原来 6°，抬一边肩时只有 1.4~4.7°）
    head_roll_deg=10.0, shoulder_tilt_deg=2.5,
    # 扭头：鼻子偏离两眼中点的距离 / 两眼间距。约 0.35×tan(转角)（见 TURN_K），0.12 约 20°
    head_turn=0.12,
    # 低头：眼睛相对耳朵往下掉了多少（按两耳间距算）。约 0.58×sin(低头角度)，0.15 ≈ 15°
    head_down_pitch=0.15,
    # 看不到耳朵时低头的备用办法：眼睛到肩膀的高度比校准时短了多少
    head_down_neck_drop=0.20,
    # 弯腰、往后靠、滑坐（gui 2026-10-27 两次重测后定的草稿，一个人的数据）。只用头上的点：
    #   眼睛低了多少 = (校准时眼睛高度 − 现在) ÷ 校准时耳距；头的大小 = 现在耳距 ÷ 校准时耳距
    #   那两次的数：坐直 低 0~0.14 / 大小 1.01~1.13；弯腰 0.51~0.59 / 1.19~1.24；
    #   低头看键盘 0.23~0.27 / 1.13~1.15；滑坐 1.0~1.1 / 0.72~0.74；往后靠 0.31 / 0.60~0.65
    # 弯腰（gui 10-27 晚上定的）：只要眼睛整体往下走了，大概率就是弯腰了，要认出来，但轻的不用提醒；
    # 低头多半是弯腰加低头的复合动作，所以弯腰和低头各判各的、可以同时报。
    #   ≥0.1 个耳距就记成弯腰（gui 10-27 定的；坐直时眼睛本身在 0~0.14 之间晃，所以坐直也会有一部分时间记成轻弯腰，
    #   只记录不提醒）；≥0.4 才参与提醒，并且这时不再报头前伸（头变大是弯腰带来的）
    #   头大小下限 10-27 晚从 0.9 改到 0.8，和往后靠接上：gui p1 弯腰时头变小到 0.82~0.87（摄像头在笔记本上，
    #   弯下去头反而离镜头远了一点），0.9 会让这种弯腰掉进 0.8~0.9 的空档什么都不报
    slouch_eye_drop=0.1, slouch_alert_eye_drop=0.4, slouch_head_size=0.8,
    # 头前伸“只看头”的办法（gui 10-27 p5：弯腰加伸头、肩膀出画，头比校准大 26%~30%，眼睛只低 0.2~0.3）：
    #   头变大 − 1 > a + b × 眼睛低了多少，也就是头变大的量超过弯腰本身会带来的那部分。
    #   gui 一个人的数：坐直 眼低 0~0.14/头 1.01~1.13；低头看键盘 0.25/1.13~1.15；弯腰 0.55/1.19~1.24；p5 0.25/1.26~1.30。
    #   a=0.12、b=0.4 时：坐直最多 0.13 < 0.12+0.056；看键盘 0.15 < 0.22；弯腰 0.24 < 0.34；p5 0.26~0.30 > 0.22。
    #   不用肩膀，肩膀出画也能判；低头、弯腰时照常判（gui 要复合动作两个一起报）。
    #   在 gui 10-27 两小时的 log 里重放：平时坐着没有一分钟误报，只在故意往屏幕凑的几段触发
    forward_head_only=0.12, forward_head_only_per_drop=0.4,
    lean_head_size=0.8, lean_eye_drop_max=0.45,     # 往后靠：头变小，眼睛没怎么低
    slide_eye_drop=0.6)                             # 滑坐：头变小，眼睛低了很多（0.45~0.6 之间先不报）

NAMES = {"forward_head": "头前伸", "shrug": "耸肩", "head_tilt": "歪头", "uneven_shoulders": "高低肩",
         "head_turn": "扭头", "head_down": "低头", "slouch": "弯腰", "lean_back": "往后靠", "slide": "滑坐"}
SHORT = {"forward_head": "fwd", "shrug": "shrug", "head_tilt": "tilt", "uneven_shoulders": "shoulders",
         "head_turn": "turn", "head_down": "down", "slouch": "slouch", "lean_back": "lean", "slide": "slide"}
FORWARD_BY = {"size": "头变大", "nose": "鼻子沉", "head": "头比弯腰该有的还大"}
LABELS = {ord("0"): "normal", ord("1"): "forward_head", ord("2"): "shrug", ord("3"): "head_tilt",
          ord("4"): "head_turn", ord("5"): "head_down", ord("6"): "uneven_shoulders"}
METRIC_KEYS = ["shoulder_width", "shoulder_mid_y", "neck_ratio", "shoulder_tilt_deg", "ear_span", "eye_span",
               "head_roll_deg", "head_turn", "head_pitch", "nose_angle", "ear_sh_l", "ear_sh_r"]
# 自测要用、平时判断不用的量：左右肩膀各自的高度（像素，y 向上），只跟自测开始时比
SELFTEST_EXTRA = ["sh_y_l", "sh_y_r"]
# 只记录、暂时不拿来判断的原始值（像素）：两眼中点的高度（y 向上）和两耳间距。
# 2026-10-26 gui 提出"整个头往下掉但角度没变 = 塌下去/驼背；头往下掉而且变小 = 滑坐靠椅背"，
# 要先录一轮数据看这两个数怎么变，再定规则。校准时的值存在 baseline.json 的 _raw 里。
RAW_KEYS = ["eye_y_px", "ear_px"]
# log.csv 里每次判断记的数。后三个带方向，正数都是往你自己的左边（见 assess）
LOG_METRICS = ["head_growth", "nose_angle_delta", "head_pitch_delta", "shoulder_rise", "neck_drop",
               "ear_sh_l_drop", "ear_sh_r_drop", "head_roll_delta", "shoulder_tilt_delta", "head_turn_delta",
               "tilt_to_left_deg", "turn_to_left", "left_shoulder_up_deg",
               "eye_drop", "head_size"]  # 弯腰/往后靠/滑坐用的两个数（见 TH），2026-10-27 加
# persons：这一秒画面里有几个人。超过一个人就暂停判断，只写时间和人数，其他列空（gui 2026-10-27 定的，
# 家里只有一个人没法实测两个人的情况，所以先用最稳的办法：只有一个人时才判）。
# 7 个点的原始坐标（像素，x 向右、y 向下，和画面一样；可信度不够的点留空）。gui 2026-10-27 要的：
# 规则改了可以拿原始点重算，不用重录。这是这一帧的点，没有平滑。
KP_NAMES = ["nose", "l_eye", "r_eye", "l_ear", "r_ear", "l_sh", "r_sh"]
KP_COLS = [f"{n}_{a}" for n in KP_NAMES for a in ("x", "y")]
LOG_COLS = (["time", "label", "issues", "severity"] + LOG_METRICS + RAW_KEYS + KP_COLS
            + ["persons", "infer_ms", "algo_version", "source"])
# 判断规则或门槛一改，就改这个版本号：改的那天的日期，同一天再改就加 .2、.3。
# 每条记录都带上它，后面再接一段门槛算出来的指纹，门槛改了、忘了改版本号也分得出来。
# 算法改过以后，前后的数据要分得清才能比（研究要用）。
ALGO_VERSION = "2026.10.27.6"
# 点是从哪来的：现在是 Mac 摄像头加原版模型；以后板子发来的点写成 board:… 。启动时按 --model 改
SOURCE = "mac:yolo11n-pose.pt"


def algo_version():
    """版本号加门槛指纹，比如 2026.10.26+3f9a1c2e。"""
    params = {"TH": TH, "TURN_K": TURN_K, "PITCH_K": PITCH_K, "ST_MOVE_MIN": ST_MOVE_MIN, "SIDE_TOL": SIDE_TOL,
              "follow": [FOLLOW_POSTURE_S, FOLLOW_SIT_S, CORRECTED_S, AWAY_S]}
    return ALGO_VERSION + "+" + hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]


# ---------- 给人看的提示 ----------
# 打包成应用以后没有终端：要让人看到的提示，除了照常打出来（应用模式下写进“运行记录.txt”），
# 还显示在窗口底部。只给开发看的（每次判断的结果、自测每一步的数）照常用 print。
APP = False             # 应用模式，main 里按参数打开
MSGS = deque(maxlen=3)  # 最近几条提示：(时间, 文字)


def say(text):
    print(text)
    MSGS.append((time.time(), text))


def line_angle(a, b):
    (ax, ay), (bx, by) = (a, b) if a[0] <= b[0] else (b, a)
    return math.degrees(math.atan2(by - ay, bx - ax))


def nose_shoulder_angle(n, a, b):
    """从鼻子往两边肩膀各连一条线，两条线的夹角（度）。鼻子越往两肩之间沉，夹角越大，
    沉到肩线以下时超过 180°。离屏幕远近不影响这个角度。"""
    (ax, ay), (bx, by) = sorted([a, b])
    ux, uy, vx, vy = ax - n[0], ay - n[1], bx - n[0], by - n[1]
    ang = math.degrees(math.atan2(abs(ux * vy - uy * vx), ux * vx + uy * vy))
    above = (bx - ax) * (n[1] - ay) - (by - ay) * (n[0] - ax) > 0  # 鼻子在肩线上方
    return ang if above else 360 - ang


def compute_metrics(xy, conf, img_h):
    """xy: 17x2 像素坐标（y 向下），conf: 17 个可信度。
    返回 dict（看不到的项是 None），连眼睛和耳朵都看不到时返回 None。
    看不到肩膀时，只关掉要用肩膀的几项（头前伸、耸肩、高低肩），头部的几项照常算。"""
    ok = lambda i: conf[i] > MIN_SCORE
    P = lambda i: (float(xy[i][0]), float(img_h - xy[i][1]))  # 翻成 y 向上
    eyes, ears = ok(L_EYE) and ok(R_EYE), ok(L_EAR) and ok(R_EAR)
    if not (eyes or ears):
        return None
    m = dict.fromkeys(METRIC_KEYS + SELFTEST_EXTRA + RAW_KEYS)
    if eyes:
        m["eye_y_px"] = (P(L_EYE)[1] + P(R_EYE)[1]) / 2
    if ears:
        m["ear_px"] = math.dist(P(L_EAR), P(R_EAR))

    if eyes:
        m["head_roll_deg"] = line_angle(P(L_EYE), P(R_EYE))
        if ok(NOSE):
            # 扭头：鼻子沿两眼连线方向偏离两眼中点多少（按眼距算）。
            # 沿眼线量、不是沿画面横向量，所以歪头时鼻子跟着转，不会被当成扭头
            (ax, ay), (bx, by) = sorted([P(L_EYE), P(R_EYE)])
            span = max(1.0, math.hypot(bx - ax, by - ay))
            ux, uy = (bx - ax) / span, (by - ay) / span
            vx, vy = P(NOSE)[0] - (ax + bx) / 2, P(NOSE)[1] - (ay + by) / 2
            m["head_turn"] = (vx * ux + vy * uy) / span
    else:
        m["head_roll_deg"] = line_angle(P(L_EAR), P(R_EAR))

    # 低头：眼睛在耳朵前面，低头时眼睛会往耳朵下面掉。
    # 量的是“两眼中点到两耳连线的垂直距离 / 两耳间距”，眼睛在耳线上方为正。
    # 用耳线当基准，所以歪头不影响；按耳距算比例，所以离屏幕远近不影响。
    if eyes and ears:
        (lx, ly), (rx, ry) = sorted([P(L_EAR), P(R_EAR)])
        span = math.hypot(rx - lx, ry - ly)
        if span > 1:
            nx, ny = -(ry - ly) / span, (rx - lx) / span  # 垂直于耳线、朝上
            ex = (P(L_EYE)[0] + P(R_EYE)[0]) / 2 - (lx + rx) / 2
            ey = (P(L_EYE)[1] + P(R_EYE)[1]) / 2 - (ly + ry) / 2
            m["head_pitch"] = (ex * nx + ey * ny) / span

    edge = SHOULDER_EDGE * img_h
    if (ok(L_SH) and ok(R_SH) and math.dist(P(L_SH), P(R_SH)) >= MIN_SHOULDER_PX
            and P(L_SH)[1] > edge and P(R_SH)[1] > edge):
        ls, rs = P(L_SH), P(R_SH)
        w = math.dist(ls, rs)  # 肩宽当尺子，离屏幕远近不影响比例
        sy = (ls[1] + rs[1]) / 2
        if eyes:
            head_y = (P(L_EYE)[1] + P(R_EYE)[1]) / 2
        elif ok(NOSE):
            head_y = P(NOSE)[1]
        else:
            head_y = (P(L_EAR)[1] + P(R_EAR)[1]) / 2
        m.update(shoulder_width=w, shoulder_mid_y=sy, neck_ratio=(head_y - sy) / w,
                 shoulder_tilt_deg=line_angle(ls, rs), sh_y_l=ls[1], sh_y_r=rs[1])
        if ears:
            m["ear_span"] = math.dist(P(L_EAR), P(R_EAR)) / w
            # 每一边肩膀到两耳中点的高度。耸肩时两边都变短，高低肩时只有一边变。
            # 用两耳中点、不用同侧耳朵：歪头时一边耳朵下来、一边上去，中点基本不动，歪头就不会搅乱耸肩
            ear_y = (P(L_EAR)[1] + P(R_EAR)[1]) / 2
            m["ear_sh_l"] = (ear_y - ls[1]) / w
            m["ear_sh_r"] = (ear_y - rs[1]) / w
        if eyes:
            m["eye_span"] = math.dist(P(L_EYE), P(R_EYE)) / w
        if ok(NOSE):
            m["nose_angle"] = nose_shoulder_angle(P(NOSE), ls, rs)
    return m


def median_of(ms, keys=METRIC_KEYS):
    """最近几次的中位数。某一项要在超过一半的次数里都有才算，不然当看不见：
    不然肩膀刚出画面的几秒，还会拿前几次的肩膀算“头变大”（gui 10-27 弯腰下去的过程里就被报成了头前伸）。"""
    out = {}
    for k in keys:
        vals = [m[k] for m in ms if m.get(k) is not None]
        out[k] = statistics.median(vals) if vals and len(vals) * 2 > len(ms) else None
    return out


def assess(m, b):
    """返回 (问题列表, 程度 0..1, 各项数值)。看不到的项是 None，不参与判断。"""
    issues, sev, d = [], 0.0, {}
    score = lambda ex, th: min(1.0, max(0.0, (ex - th) / th))

    def delta(k):
        return None if m.get(k) is None or b.get(k) is None else m[k] - b[k]

    def shrink(k):  # 比校准时短了百分之几
        return None if m.get(k) is None or not b.get(k) or b[k] <= 0 else 1 - m[k] / b[k]

    def flag(k, s):
        nonlocal sev
        issues.append(k)
        sev = max(sev, 0.5 + 0.5 * s)

    # 扭头和歪头：都和校准时比。
    # 大幅扭头时两眼连线看起来也会歪，大幅歪头时鼻子看起来也会偏，所以两个都超过时只留更明显的那个
    # （按超过各自门槛的倍数比）。你第一轮的数据里，扭头时有一半时间同时报了歪头，反过来也一样。
    # 带方向的量，正数都是往你自己的左边：头往左歪、头往左转、左肩比右肩高。
    # 前提是画面没镜像（Mac 自带摄像头用 OpenCV 读是这样，10-26 第一次实测自测也对得上）。
    # 判断还是只看大小，方向只记下来、写进提醒
    turn_s, roll_s = delta("head_turn"), delta("head_roll_deg")
    d["turn_to_left"] = turn_s
    d["tilt_to_left_deg"] = None if roll_s is None else -roll_s
    turn = abs(turn_s) if turn_s is not None else None
    roll = abs(roll_s) if roll_s is not None else None
    d["head_turn_delta"], d["head_roll_delta"] = turn, roll
    turned = turn is not None and turn > TH["head_turn"]
    tilted = roll is not None and roll > TH["head_roll_deg"]
    if turned and tilted:
        if turn / TH["head_turn"] >= roll / TH["head_roll_deg"]:
            tilted = False
        else:
            turned = False
    # 扭头时远侧的耳朵被挡住、眼距变窄，头前伸和低头都算不准，所以扭头时这两项不判
    if turned:
        flag("head_turn", score(turn, TH["head_turn"]))

    # 耸肩：左右两边肩膀都离耳朵变近，并且肩膀确实往上走了（要看得到肩膀）。
    #   只有一边变近是高低肩，不算；
    #   头往下沉也会让两边都变近，但肩膀没上抬，不算；椅子调高时头肩一起上去，距离不变，也不算。
    # 用耳朵不用眼睛：低头时眼睛会往下掉，耳朵基本不动。看不到耳朵时才退回用眼睛（只能看两边平均）。
    rise = None
    if m.get("shoulder_mid_y") is not None and b.get("shoulder_mid_y") is not None and b.get("shoulder_width"):
        rise = (m["shoulder_mid_y"] - b["shoulder_mid_y"]) / b["shoulder_width"]
    raw_b = b.get("_raw") or b  # 校准时的眼睛高度和耳距（像素）；旧校准没有就退回用 b 本身（里面没有，相应项不判）

    def ear_sh_shrink(k):
        """耳到肩的高度比校准时短了百分之几。ear_sh_* 是按肩宽算的比例，这里乘回肩宽、改按耳距算
        （见 TH 里耸肩的说明）；哪边缺耳距就退回按肩宽算。"""
        if m.get(k) is None or not b.get(k) or b[k] <= 0:
            return None
        if m.get("ear_px") and raw_b.get("ear_px") and m.get("shoulder_width") and b.get("shoulder_width"):
            now = m[k] * m["shoulder_width"] / m["ear_px"]
            cal = b[k] * b["shoulder_width"] / raw_b["ear_px"]
            return 1 - now / cal if cal > 0 else None
        return 1 - m[k] / b[k]

    ear_l, ear_r, eye_neck = ear_sh_shrink("ear_sh_l"), ear_sh_shrink("ear_sh_r"), shrink("neck_ratio")
    # 两边都要变近，所以看变得少的那一边
    neck_drop = min(ear_l, ear_r) if ear_l is not None and ear_r is not None else eye_neck
    d.update(shoulder_rise=rise, neck_drop=neck_drop, ear_sh_l_drop=ear_l, ear_sh_r_drop=ear_r)
    shrug = (rise is not None and neck_drop is not None
             and rise > TH["shrug_rise"] and neck_drop > TH["shrug_neck_drop"])
    if shrug:
        flag("shrug", score(rise, TH["shrug_rise"]))

    # 弯腰、往后靠、滑坐（见 TH 里的说明）。校准时的眼睛高度和耳距在 baseline 的 _raw 里，旧校准没有就不判。
    #   往后靠、滑坐时眼耳夹角也会变（滑坐 −0.13~−0.19），所以这两种时不报低头和头前伸；
    #   弯腰时头会变大（+20%）、鼻子也沉，所以弯腰时不报头前伸；但到了低头门槛就算低头，不算弯腰。
    #   扭头时耳距不准，这三项都不判。
    eye_drop = head_size = None
    if m.get("eye_y_px") is not None and raw_b.get("eye_y_px") is not None and raw_b.get("ear_px"):
        eye_drop = (raw_b["eye_y_px"] - m["eye_y_px"]) / raw_b["ear_px"]
    if m.get("ear_px") and raw_b.get("ear_px"):
        head_size = m["ear_px"] / raw_b["ear_px"]
    d["eye_drop"], d["head_size"] = eye_drop, head_size
    small = head_size is not None and eye_drop is not None and head_size < TH["lean_head_size"] and not turned
    lean_back = small and eye_drop <= TH["lean_eye_drop_max"]
    slide = small and eye_drop >= TH["slide_eye_drop"]
    if lean_back:
        flag("lean_back", score(1 - head_size, 1 - TH["lean_head_size"]))
    if slide:
        flag("slide", score(eye_drop, TH["slide_eye_drop"]))

    # 低头：眼睛往耳朵下面掉（眼耳夹角变了）。看不到耳朵时才退回用眼睛到肩膀的高度
    pitch = delta("head_pitch")
    d["head_pitch_delta"] = pitch
    down = False
    if not turned and not lean_back and not slide:
        if pitch is not None:
            down = -pitch > TH["head_down_pitch"]
            if down:
                flag("head_down", score(-pitch, TH["head_down_pitch"]))
        elif eye_neck is not None and not shrug and eye_neck > TH["head_down_neck_drop"]:
            down = True
            flag("head_down", score(eye_neck, TH["head_down_neck_drop"]))

    # 弯腰：整个头下沉但没变小（没小到往后靠的程度）。和低头各判各的（低头多半是弯腰加低头）。
    # 程度按提醒门槛算：0.4 以下是轻的，程度 <0.5，主循环不拿它提醒
    slouch = (eye_drop is not None and head_size is not None and not turned and not small
              and eye_drop >= TH["slouch_eye_drop"] and head_size >= TH["slouch_head_size"])
    slouch_deep = slouch and eye_drop >= TH["slouch_alert_eye_drop"]
    d["slouch_alert"] = slouch_deep
    if slouch:
        issues.append("slouch")
        sev = max(sev, 0.5 + 0.5 * score(eye_drop, TH["slouch_alert_eye_drop"]) if slouch_deep
                  else 0.25 + 0.25 * (eye_drop - TH["slouch_eye_drop"]) / (TH["slouch_alert_eye_drop"] - TH["slouch_eye_drop"]))

    # 头前伸（要看得到肩膀），两个办法任一个超过就算：
    #   头变大：头相对肩宽变大，也就是头往屏幕凑
    #   鼻子沉：鼻子到两肩的夹角张开，也就是鼻子往两肩之间沉。
    #          要看得到眼耳夹角才判（不然分不清是不是低头）；肩膀在往上走时也不判（那是肩膀在动，不是头）
    # 低头时鼻子也会沉，但眼耳夹角也会变：已经判成低头，就不再报头前伸。
    # （你故意伸头时眼耳也会掉 0.09~0.11，比低头的 0.18~0.23 小得多，所以只要没到低头就照常判伸头）
    g = None
    if m.get("ear_span") and b.get("ear_span"):
        g = m["ear_span"] / b["ear_span"] - 1
    elif m.get("eye_span") and b.get("eye_span"):
        g = m["eye_span"] / b["eye_span"] - 1
    nose = delta("nose_angle")
    d["head_growth"], d["nose_angle_delta"] = g, nose
    by_size = g is not None and g > TH["forward_head_growth"]
    by_nose = (nose is not None and nose > TH["forward_head_nose_deg"] and pitch is not None
               and (rise is None or rise < TH["shrug_rise"] / 2))
    # 第三个办法只看头：头变大的量超过弯腰本身会带来的那部分（见 TH 里 forward_head_only 的说明）。
    # 不用肩膀，低头、弯腰时也照常判（gui 10-27：弯腰加伸头是复合动作，要两个一起报）
    head_extra = None
    if head_size is not None and eye_drop is not None:
        head_extra = head_size - 1 - TH["forward_head_only_per_drop"] * max(0.0, eye_drop)
    # 看得到肩膀时以肩膀为准：整个人往前坐、肩膀和头一起变大（头相对肩宽没大），不算伸头；
    # 肩膀出画时只能看头，这时整个人靠近 12% 以上也会被当成伸头（只有一个人的数据，先这样）
    by_head = (head_extra is not None and head_extra > TH["forward_head_only"]
               and (g is None or g > TH["forward_head_growth"] / 2))
    d["forward_by"] = []
    # 头变小了（离屏幕远了）就不可能是往屏幕凑。头变大、鼻子沉这两条要肩膀，低头和深弯腰时会被带着变，不算
    by_size, by_nose = by_size and not down and not slouch_deep, by_nose and not down and not slouch_deep
    if not turned and not small and (by_size or by_nose or by_head):
        d["forward_by"] = [k for k, on in (("size", by_size), ("nose", by_nose), ("head", by_head)) if on]
        flag("forward_head", max(score(g, TH["forward_head_growth"]) if by_size else 0.0,
                                 score(nose, TH["forward_head_nose_deg"]) if by_nose else 0.0,
                                 score(head_extra, TH["forward_head_only"]) if by_head else 0.0))

    # 歪头：两眼连线比校准时歪了几度（和校准比，所以摄像头本身歪着也没关系）
    if tilted:
        flag("head_tilt", score(roll, TH["head_roll_deg"]))

    # 高低肩：左右两个肩膀点的高度差，换算成肩线的角度，比校准时多歪了几度。
    # 歪头时肩膀常常跟着歪（你第一轮歪头时肩线歪了 4~8°），所以歪头时不单独报高低肩
    tilt = delta("shoulder_tilt_deg")
    d["left_shoulder_up_deg"] = tilt
    tilt = abs(tilt) if tilt is not None else None
    d["shoulder_tilt_delta"] = tilt
    if tilt is not None and tilt > TH["shoulder_tilt_deg"] and not tilted:
        flag("uneven_shoulders", score(tilt, TH["shoulder_tilt_deg"]))
    return issues, (sev if issues else 0.0), d


# ---------- 提醒规则 ----------
class ShareAlerts:
    """看过去 window 秒：某个问题出现的次数占到 share 以上，就提醒一次。
    占比降到 30% 以下才重新开始算，这样同一段坏姿势不会反复提醒。
    和“连续保持多久”不同，想事情时的小动作不会把计时清零。"""

    def __init__(self, window, share):
        self.window, self.share = window, share
        self.recent = deque()  # (时间, 这次判断出的问题)
        self.alerted = set()

    def reset(self):
        self.recent.clear()
        self.alerted.clear()

    def update(self, now, issues):
        self.recent.append((now, set(issues)))
        while now - self.recent[0][0] > self.window:
            self.recent.popleft()
        covered = now - self.recent[0][0] >= 0.8 * self.window  # 数据够不够一整段
        n = len(self.recent)
        shares = {k: sum(k in s for _, s in self.recent) / n for k in NAMES}
        fired = []
        for k, sh in shares.items():
            if covered and sh >= self.share and k not in self.alerted:
                self.alerted.add(k)
                fired.append((k, sh))
            elif sh < 0.3:
                self.alerted.discard(k)
        return shares, fired


class SitTimer:
    """连续坐满 limit 秒提醒一次起来活动；看不到人超过 away_reset 秒，算起来活动过，重新计时。"""

    def __init__(self, limit, away_reset=120):
        self.limit, self.away_reset = limit, away_reset
        self.since = self.last_seen = None
        self.alerted = False

    def update(self, now, present):
        if not present:
            return None
        if self.since is None or now - self.last_seen > self.away_reset:
            self.since, self.alerted = now, False
        self.last_seen = now
        if not self.alerted and now - self.since >= self.limit:
            self.alerted = True
            return now - self.since
        return None

    def minutes(self, now):
        return 0.0 if self.since is None else (now - self.since) / 60


# ---------- 提醒之后有没有改过来（研究要用） ----------
# 每次提醒都跟着看一段时间，有结果了在 <日志名>-alerts.csv 里记一行：提醒了什么、之后改没改、用了几秒。
# 下面几个数是我定的起步值，还没验证：
FOLLOW_POSTURE_S = 300  # 姿势提醒之后看 5 分钟
FOLLOW_SIT_S = 600      # 久坐提醒之后看 10 分钟
CORRECTED_S = 10        # 那个问题连续 10 秒没再出现，算改过来了。判断用的是最近 5 次的中位数，本身会晚几秒
AWAY_S = 60             # 连续 60 秒看不到人，算离开了座位（设计文档里“赌气”那一级也是确认离开 1 分钟）
ALERT_COLS = ["time", "kind", "issue", "direction", "share", "window_s", "sit_min", "channel",
              "outcome", "outcome_time", "seconds_to_outcome", "algo_version", "source"]
OUTCOME_CN = {"corrected": "改过来了", "left_seat": "离开了座位", "stood_up": "起来活动了",
              "not_corrected": "一直没改", "interrupted": "中途开始了自测或校准，没看完", "ended": "程序关了，没看完"}


class AlertFollow:
    """跟着每次提醒看结果。姿势提醒：问题连续 CORRECTED_S 秒没出现就是改过来了；
    久坐提醒：连续 AWAY_S 秒看不到人就是起来了。看满时间还没有就是没改。"""

    def __init__(self, writer=None, f=None):
        self.writer, self.f = writer, f
        self.pending = []

    def add(self, now, kind, issue="", direction="", share=None, window_s=None, sit_min=None):
        self.pending.append(dict(time=now, kind=kind, issue=issue, direction=direction, share=share,
                                 window_s=window_s, sit_min=sit_min, ok_since=None, away_since=None))

    def update(self, now, present, issues=None):
        """每识别完一次调用。present：看没看到人；issues：这次判出的问题，没判（比如看不到脸）就是 None。"""
        for p in list(self.pending):
            if present:
                p["away_since"] = None
            elif p["away_since"] is None:
                p["away_since"] = now
            away = p["away_since"] is not None and now - p["away_since"] >= AWAY_S
            if p["kind"] == "sitting":
                if away:
                    self._close(p, "stood_up", p["away_since"])
                elif now - p["time"] >= FOLLOW_SIT_S:
                    self._close(p, "not_corrected", now)
                continue
            if not present:
                p["ok_since"] = None  # 看不到人的时候不知道改没改，重新算
            elif issues is not None:
                if p["issue"] in issues:
                    p["ok_since"] = None
                elif p["ok_since"] is None:
                    p["ok_since"] = now
            if p["ok_since"] is not None and now - p["ok_since"] >= CORRECTED_S:
                self._close(p, "corrected", p["ok_since"])
            elif away:
                self._close(p, "left_seat", p["away_since"])
            elif now - p["time"] >= FOLLOW_POSTURE_S:
                self._close(p, "not_corrected", now)

    def close_all(self, now, outcome):
        for p in list(self.pending):
            self._close(p, outcome, now)

    def _close(self, p, outcome, at):
        self.pending.remove(p)
        what = side_name(p["issue"], p["direction"]) if p["issue"] else "久坐"
        when = f"，提醒后 {at - p['time']:.0f} 秒" if outcome in ("corrected", "left_seat", "stood_up") else ""
        say(f"提醒结果：{what} → {OUTCOME_CN[outcome]}{when}")
        if self.writer:
            fmt = lambda v, f: "" if v is None else format(v, f)
            self.writer.writerow([f"{p['time']:.2f}", p["kind"], p["issue"], p["direction"], fmt(p["share"], ".2f"),
                                  fmt(p["window_s"], ".0f"), fmt(p["sit_min"], ".1f"), "notify", outcome,
                                  f"{at:.2f}", f"{at - p['time']:.1f}", algo_version(), SOURCE])
            self.f.flush()


# ---------- 自测 ----------
# 跟着窗口里的大字做 7 个动作（左右交替，做两轮），量能动多大、左右差多少。
# 第二轮每个动作做完马上问：刚才做的时候，左边、右边各疼几分、紧几分（0–10）。
# 每次一行存进 selftest.csv，以后用来跟自己以前比、出周报、给放松建议。只跟自己比，不是诊断。
SELFTEST_STEPS = [
    # (动作, 大字, 小字, 找不到中文字体时显示的英文)
    ("tilt_l", "头往左歪", "左耳朝左肩靠，肩膀别动", "Tilt head to YOUR LEFT"),
    ("tilt_r", "头往右歪", "右耳朝右肩靠，肩膀别动", "Tilt head to YOUR RIGHT"),
    ("turn_l", "头往左转", "像回头看左后方", "Turn head to YOUR LEFT"),
    ("turn_r", "头往右转", "像回头看右后方", "Turn head to YOUR RIGHT"),
    ("down", "低头", "下巴往胸口贴", "Look DOWN, chin to chest"),
    ("up", "抬头", "往天花板看", "Look UP at the ceiling"),
    ("shrug", "耸肩", "两边肩膀一起往耳朵提", "SHRUG both shoulders up"),
]
SELFTEST_ROUNDS = 2
SELFTEST_KEYS = ["tilt_l", "tilt_r", "turn_l", "turn_r", "down", "up", "shrug_l", "shrug_r"]
SELFTEST_NAMES = {"tilt_l": ("头往左歪", "°"), "tilt_r": ("头往右歪", "°"), "turn_l": ("头往左转", "°"),
                  "turn_r": ("头往右转", "°"), "down": ("低头", "°"), "up": ("抬头", "°"),
                  "shrug_l": ("左肩上抬", "%"), "shrug_r": ("右肩上抬", "%")}
# 最后一轮每个动作做完马上问几道题（gui 2026-10-27 定的草案）。原则：
#   疼按“被拉长的一边 / 被挤的一边”问，不按左右问——歪头时被拉的那边必然紧、被挤的那边必然不紧，按左右问没有信息；
#   拉长侧疼偏肌肉、被挤侧疼偏关节，是临床区分的常用思路，这里只做关联、不下结论；
#   “拉扯感”是对比着打的：往左歪先打右侧拉扯感当基准，往右歪时屏幕上显示“刚才右侧是 X”再打左侧；
#   转头时前后的肌肉方向相反（左转：左胸锁乳突肌和右头夹肌、右肩胛提肌被拉长），所以转头要问位置（前/后、左/右）；
#   低头、抬头是前后而不是左右；耸肩两边都在收缩、没有被拉长的一边，只问疼；
#   最后问一句“跟开始比更舒服 / 一样 / 更疼，哪个动作最明显”（临床的症状反应，直接决定放松建议推哪个方向）。
#   0 = 没有，10 = 最重；直接回车就是 0，所以“有没有”和“几分”一次问完。只跟自己比，不是诊断。
class Q:
    """一道题。kind：num = 0–10 分；choice = 从 opts 里选一个（按数字键直接选）。
    short：结果里的短名。cond = (前一题代号, 跳过时的默认值)：前一题是 0 / none / same 就不问，直接填默认值。
    compare = (动作, 题目代号, 中文, 英文)：屏幕上显示那一题的答案当对比。opts：[(代号, 中文, 英文)]，编号从 start 起。"""

    def __init__(self, key, cn, en, kind="num", short=None, short_en=None, hint="", opts=None, start=1, cond=None,
                 compare=None):
        self.key, self.cn, self.en, self.kind, self.hint = key, cn, en, kind, hint
        self.short, self.short_en = short or cn, short_en or en
        self.opts, self.start, self.cond, self.compare = opts or [], start, cond, compare

    def label(self, v, cn=True):
        """答案写成人看的字：分数原样，选项换成文字。"""
        if v is None:
            return "–" if cn else "-"
        for code, c, e in self.opts:
            if code == v:
                return c if cn else e
        return str(v)


WHERE_FB = [("FL", "前左", "front left"), ("FR", "前右", "front right"),
            ("BL", "后左", "back left"), ("BR", "后右", "back right")]
WHERE_DOWN = [("none", "不疼", "no pain"), ("back_neck", "后颈", "back of neck"),
              ("blades", "两肩胛之间", "between shoulder blades"), ("other", "别处", "elsewhere")]
CHANGE = [("better", "更舒服", "better"), ("same", "一样", "same"), ("worse", "更疼", "worse")]
STEP_OPTS = [(s, big, en) for s, big, _, en in SELFTEST_STEPS]
FEEL_Q = {
    "tilt_l": [
        Q("pain_long", "右侧被拉长的地方疼几分", "Pain on the RIGHT (stretched side)?", short="右侧拉长处疼",
          short_en="R stretched pain", hint="被拉长：右上斜方肌、右肩胛提肌、右斜角肌、右胸锁乳突肌"),
        Q("pain_short", "左侧被挤的地方疼几分", "Pain on the LEFT (squeezed side)?", short="左侧被挤处疼",
          short_en="L squeezed pain", hint="被挤：左侧同名肌、左侧小关节"),
        Q("pull_long", "右侧拉扯感几分", "Stretch feeling on the RIGHT?", short="右侧拉扯", short_en="R stretch",
          hint="这个当基准，往右歪时拿来对比")],
    "tilt_r": [
        Q("pain_long", "左侧被拉长的地方疼几分", "Pain on the LEFT (stretched side)?", short="左侧拉长处疼",
          short_en="L stretched pain", hint="被拉长：左上斜方肌、左肩胛提肌、左斜角肌、左胸锁乳突肌"),
        Q("pain_short", "右侧被挤的地方疼几分", "Pain on the RIGHT (squeezed side)?", short="右侧被挤处疼",
          short_en="R squeezed pain", hint="被挤：右侧同名肌、右侧小关节"),
        Q("pull_long", "左侧拉扯感几分", "Stretch feeling on the LEFT?", short="左侧拉扯", short_en="L stretch",
          compare=("tilt_l", "pull_long", "刚才往左歪时右侧拉扯是", "Tilting left, right side was"))],
    "turn_l": [
        Q("pain", "疼几分", "Pain?", short="疼", short_en="pain",
          hint="被拉长：左胸锁乳突肌、右头夹肌、右肩胛提肌；收缩：右胸锁乳突肌、左头夹肌"),
        Q("pain_where", "疼在哪", "Where is the pain?", "choice", short="疼在", short_en="pain at", opts=WHERE_FB,
          cond=("pain", "none")),
        Q("pull", "拉扯感几分", "Stretch feeling?", short="拉扯", short_en="stretch"),
        Q("pull_where", "拉扯在哪", "Where is the stretch?", "choice", short="拉扯在", short_en="stretch at",
          opts=WHERE_FB, cond=("pull", "none"))],
    "turn_r": [
        Q("pain", "疼几分", "Pain?", short="疼", short_en="pain",
          hint="被拉长：右胸锁乳突肌、左头夹肌、左肩胛提肌；收缩：左胸锁乳突肌、右头夹肌"),
        Q("pain_where", "疼在哪", "Where is the pain?", "choice", short="疼在", short_en="pain at", opts=WHERE_FB,
          cond=("pain", "none")),
        Q("pull", "拉扯感几分", "Stretch feeling?", short="拉扯", short_en="stretch",
          compare=("turn_l", "pull", "刚才往左转时拉扯是", "Turning left it was")),
        Q("pull_where", "拉扯在哪", "Where is the stretch?", "choice", short="拉扯在", short_en="stretch at",
          opts=WHERE_FB, cond=("pull", "none"))],
    "down": [
        Q("pull", "后颈拉扯感几分", "Stretch at the back of the neck?", short="后颈拉扯", short_en="back-neck stretch",
          hint="被拉长：颈伸肌群、上斜方肌"),
        Q("pain_where", "哪里疼", "Where is the pain?", "choice", short="疼在", short_en="pain at", opts=WHERE_DOWN,
          start=0),
        Q("pain", "疼几分", "How much pain?", short="疼", short_en="pain", cond=("pain_where", 0))],
    "up": [
        Q("pull", "前颈拉扯感几分", "Stretch at the front of the neck?", short="前颈拉扯", short_en="front-neck stretch",
          hint="被拉长：胸锁乳突肌、舌骨下肌"),
        Q("pain_back", "后颈有没有被挤着疼，几分", "Pinching pain at the back of the neck?", short="后颈挤疼",
          short_en="back-neck pinch", hint="被挤：后颈小关节")],
    "shrug": [
        Q("pain_l", "左肩收缩时疼几分", "Pain in the LEFT shoulder?", short="左肩疼", short_en="L shoulder pain",
          hint="上斜方肌、肩胛提肌收缩；两边都在收缩，没有被拉长的一边，所以不问拉扯"),
        Q("pain_r", "右肩收缩时疼几分", "Pain in the RIGHT shoulder?", short="右肩疼", short_en="R shoulder pain")],
    # 全部做完再问一次
    "end": [
        Q("change", "跟开始比，颈肩现在是", "Compared with the start, your neck is", "choice", short="跟开始比",
          short_en="vs start", opts=CHANGE),
        Q("change_step", "哪个动作最明显", "Which move made the difference?", "choice", short="最明显",
          short_en="most from", opts=STEP_OPTS, cond=("change", "none"))],
}
FEEL_STEPS = [s for s, *_ in SELFTEST_STEPS] + ["end"]
FEEL_KEYS = [f"{s}_{q.key}" for s in FEEL_STEPS for q in FEEL_Q[s]]
STEP_BIG = {s: big for s, big, *_ in SELFTEST_STEPS}
STEP_EN = {s: en for s, big, _, en in SELFTEST_STEPS}
SELFTEST_COLS = ["time", "local_time"] + SELFTEST_KEYS + FEEL_KEYS + ["warnings", "algo_version", "source", "raw"]
# 时间（秒）：坐正先等 1 秒再采 3 秒；每个动作先显示 3 秒提示，然后开始做，
# 看到你回正就进下一个（至少 1.5 秒，最多等 8 秒）；结果页 60 秒没按键就自动回到监测
ST_SETTLE, ST_NEUTRAL, ST_READY, ST_GO_MIN, ST_GO_MAX, ST_RESULT = 1.0, 3.0, 3.0, 1.5, 8.0, 60.0
# 至少动了这么多才算做了这个动作（我定的）：歪头 5°，转头、低头、抬头约 10°，耸肩肩宽的 2%
ST_MOVE_MIN = {"tilt": 5.0, "turn": 0.06, "down": 0.10, "up": 0.10, "shrug": 0.02}
# 往你自己的左边歪头，量出来是负的；往左转是正的。按画面几何推的，前提是画面没镜像
# （OpenCV 读 Mac 摄像头一般不镜像；10-26 在 Mac 上第一次实测没出现“方向对不上”，和这个一致）。只用来发现左右做反了
ST_EXPECT_SIGN = {"tilt_l": -1, "tilt_r": 1, "turn_l": 1, "turn_r": -1}
# 把转头、低头抬头的相对量换成大概的度数。系数是我按一般人的脸估的，没实测，只适合跟自己比：
#   转头：鼻尖比两眼往前约 2.2 厘米，两眼相距约 6.3 厘米 → 鼻子偏移 / 眼距 ≈ 0.35 × tan(转角)
#   低头抬头：眼睛比耳朵往前约 8.5 厘米，两耳相距约 14.5 厘米 → 眼耳变化 ≈ 0.58 × sin(角度)
TURN_K, PITCH_K = 0.35, 0.58
# 左右差在这以内，或者差不到大的那边的 10%，就算差不多（我定的，还没验证）
SIDE_TOL = {"°": 5.0, "%": 2.0}


def st_kind(step):
    return step.split("_")[0]


def selftest_sample(kind, m, n):
    """这一刻和自测开始时坐正比，变了多少（带正负号）。看不到返回 None。
      tilt：头相对两肩歪了几度（身子、肩膀跟着歪的那部分扣掉；坐正时没拍到肩膀就只看头）
      turn：鼻子沿两眼连线偏了多少（按眼距算）
      down / up：眼耳的变化（低头为负）
      shrug：(左肩, 右肩) 各往上抬了多少（按坐正时的肩宽算）"""
    if m is None or n is None:
        return None
    d = lambda k: None if m.get(k) is None or n.get(k) is None else m[k] - n[k]
    if kind == "tilt":
        roll = d("head_roll_deg")
        if roll is None or n.get("shoulder_tilt_deg") is None:
            return roll
        sh = d("shoulder_tilt_deg")
        return None if sh is None else roll - sh
    if kind == "turn":
        return d("head_turn")
    if kind in ("down", "up"):
        return d("head_pitch")
    w, l, r = n.get("shoulder_width"), d("sh_y_l"), d("sh_y_r")
    return None if not w or l is None or r is None else (l / w, r / w)


def selftest_mag(kind, v):
    """往要求的方向动了多少，用来找最大处、看有没有回正。"""
    if kind == "shrug":
        return (v[0] + v[1]) / 2
    if kind == "down":
        return -v
    return v if kind == "up" else abs(v)


def selftest_peak(kind, values):
    """一次动作里，取幅度最大的三分之一时刻的中位数。比直接取最大值稳，不怕偶尔一帧点跳了。"""
    if not values:
        return None
    top = sorted(values, key=lambda v: selftest_mag(kind, v), reverse=True)[:max(3, len(values) // 3)]
    if kind == "shrug":
        return statistics.median(v[0] for v in top), statistics.median(v[1] for v in top)
    return statistics.median(top)


def selftest_amount(kind, v):
    """换成报告用的数：歪头是度；转头、低头、抬头是大概的度数；耸肩是 (左, 右) 肩宽的百分之几。"""
    if v is None:
        return None
    if kind == "shrug":
        return v[0] * 100, v[1] * 100
    if kind == "tilt":
        return abs(v)
    if kind == "turn":
        return math.degrees(math.atan(abs(v) / TURN_K))
    return math.degrees(math.asin(min(1.0, max(0.0, selftest_mag(kind, v)) / PITCH_K)))


def amount_text(kind, v, cn=True):
    a = selftest_amount(kind, v)
    if a is None:
        return "看不清" if cn else "can't see"
    if kind == "shrug":
        return f"左 {a[0]:.0f}%　右 {a[1]:.0f}%" if cn else f"L {a[0]:.0f}%  R {a[1]:.0f}%"
    approx = kind != "tilt"
    return f"{'约 ' if approx else ''}{a:.0f}°" if cn else f"{'~' if approx else ''}{a:.0f} deg"


def side_text(l, r, unit, approx):
    if l is None or r is None:
        return "左右没法比"
    diff = l - r
    if abs(diff) < max(SIDE_TOL[unit], 0.1 * max(abs(l), abs(r))):
        return "左右差不多"
    return f"{'右' if diff > 0 else '左'}边少{'约' if approx else ''} {abs(diff):.0f}{unit}"


def summarize_selftest(reps):
    """把每次动作合成每一项的数（两轮平均），并挑出可疑的地方。
    返回 (SELFTEST_KEYS → 数或 None, 中文提醒, 提醒代号)。"""
    val = {(r["step"], r["round"]): r["v"] for r in reps}
    rounds = sorted({r["round"] for r in reps})
    notes, codes = [], []
    for kind, name in (("tilt", "歪头"), ("turn", "转头")):
        for rd in rounds:
            a, b = val.get((f"{kind}_l", rd)), val.get((f"{kind}_r", rd))
            if a is None or b is None:
                continue
            if a * b > 0:  # 左右两次往同一边：分不清哪次是哪边，这一轮不算
                val[(f"{kind}_l", rd)] = val[(f"{kind}_r", rd)] = None
                notes.append(f"第 {rd} 轮{name}，左右两次往同一边了，这一轮不算")
                codes.append(f"{kind}_same_side_{rd}")
            elif a * ST_EXPECT_SIGN[f"{kind}_l"] < 0:
                notes.append(f"第 {rd} 轮{name}的方向和你自己的左右对不上：可能左右做反了，或者画面是镜像的")
                codes.append(f"{kind}_flipped_{rd}")
    res = {}
    for step, big, *_ in SELFTEST_STEPS:
        kind = st_kind(step)
        got = [selftest_amount(kind, val.get((step, rd))) for rd in rounds]
        got = [g for g in got if g is not None]
        if not got:
            notes.append(f"{big}没测到")
            codes.append(f"missing_{step}")
        if kind == "shrug":
            res["shrug_l"] = statistics.mean(g[0] for g in got) if got else None
            res["shrug_r"] = statistics.mean(g[1] for g in got) if got else None
        else:
            res[step] = statistics.mean(got) if got else None
    return res, notes, codes


def selftest_lines(res, cn=True):
    """结果的几行字，窗口和终端共用。"""
    def f(x, unit, approx):
        if x is None:
            return "–" if cn else "n/a"
        if cn:
            return f"{'约' if approx else ''}{x:.0f}{unit}"
        return f"{'~' if approx else ''}{x:.0f}{'%' if unit == '%' else ' deg'}"
    out = []
    for name, en, l, r, unit, approx in (("歪头", "Tilt", "tilt_l", "tilt_r", "°", False),
                                         ("转头", "Turn", "turn_l", "turn_r", "°", True),
                                         ("耸肩（按肩宽）", "Shrug", "shrug_l", "shrug_r", "%", False)):
        L, R = f(res[l], unit, approx), f(res[r], unit, approx)
        out.append(f"{name}　左 {L}　右 {R}　{side_text(res[l], res[r], unit, approx)}" if cn
                   else f"{en}  L {L}  R {R}")
    d, u = f(res["down"], "°", True), f(res["up"], "°", True)
    out.insert(2, f"低头 {d}　抬头 {u}" if cn else f"Down {d}  Up {u}")
    return out


def feel_empty(v):
    """这个答案算不算“没有”：0 分、没疼的位置、“一样”都算。"""
    return v in (0, "none", "same")


def feel_text(step, ans, cn=True):
    """一个动作做的时候的感觉写成一句：只列有东西的，比如“右侧拉长处疼 3　右侧拉扯 6”“疼 4（后左）”。
    ans：{"pain_long": 3, ...}，没答的是 None。"""
    ans = ans or {}
    qs = FEEL_Q[step]
    vals = [ans.get(q.key) for q in qs]
    if all(v is None for v in vals):
        return "没答" if cn else "skipped"
    parts = []
    for q, v in zip(qs, vals):
        if v is None or feel_empty(v):
            continue
        if q.kind == "choice" and q.cond and parts:  # 位置跟在前一个分数后面写成括号
            parts[-1] += f"（{q.label(v, cn)}）" if cn else f" ({q.label(v, cn)})"
        else:
            parts.append(f"{q.short if cn else q.short_en} {q.label(v, cn)}")
    if parts:
        return ("　" if cn else ", ").join(parts)
    if None in vals:
        return "没答完" if cn else "not finished"
    return "都没有" if cn else "none"


def feel_compare(feel, cn=True):
    """对照着看的几行：歪头时两边被拉长时各疼几分、拉扯几分，被挤时各疼几分；转头两边的拉扯。"""
    out = []
    n = lambda v: ("–" if cn else "-") if v is None else str(v)
    tl, tr = feel.get("tilt_l") or {}, feel.get("tilt_r") or {}
    if any(v is not None for v in list(tl.values()) + list(tr.values())):
        if cn:
            out.append(f"被拉长时：右侧（往左歪）疼 {n(tl.get('pain_long'))} 拉扯 {n(tl.get('pull_long'))}；"
                       f"左侧（往右歪）疼 {n(tr.get('pain_long'))} 拉扯 {n(tr.get('pull_long'))}")
            out.append(f"被挤时：左侧（往左歪）疼 {n(tl.get('pain_short'))}；右侧（往右歪）疼 {n(tr.get('pain_short'))}")
        else:
            out.append(f"Stretched: right (tilt left) pain {n(tl.get('pain_long'))} stretch {n(tl.get('pull_long'))}; "
                       f"left (tilt right) pain {n(tr.get('pain_long'))} stretch {n(tr.get('pull_long'))}")
            out.append(f"Squeezed: left (tilt left) pain {n(tl.get('pain_short'))}; "
                       f"right (tilt right) pain {n(tr.get('pain_short'))}")
    ul, ur = feel.get("turn_l") or {}, feel.get("turn_r") or {}
    if ul.get("pull") is not None or ur.get("pull") is not None:
        where = lambda a: FEEL_Q["turn_l"][3].label(a.get("pull_where"), cn)
        at = lambda a: (f"（{where(a)}）" if cn else f" ({where(a)})") if a.get("pull") else ""
        out.append((f"转头拉扯：左转 {n(ul.get('pull'))}{at(ul)}　右转 {n(ur.get('pull'))}{at(ur)}") if cn else
                   f"Turn stretch: left {n(ul.get('pull'))}{at(ul)}, right {n(ur.get('pull'))}{at(ur)}")
    return out


def feel_change_text(feel, cn=True):
    """最后那句“跟开始比”的结论，没答返回 None。"""
    end = feel.get("end") or {}
    c = end.get("change")
    if c is None:
        return None
    q1, q2 = FEEL_Q["end"]
    text = ("跟开始比：" if cn else "Compared with the start: ") + q1.label(c, cn)
    if end.get("change_step") not in (None, "none"):
        text += (f"，最明显的是“{q2.label(end['change_step'], cn)}”" if cn
                 else f", mostly from {q2.label(end['change_step'], cn)}")
    return text


def save_selftest(path, row):
    """追加一行，返回上一次的记录（dict），没有返回 None。
    之前版本写的列不一样：对得上的列（时间、各个角度）换成新格式接着用，好继续跟以前比；
    有新版本不再用的列（比如以前结尾问的“最近几天”打分），先把整个旧文件原样备份一份，一个数都不丢。"""
    rows = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
        if rows and rows[0] != SELFTEST_COLS:
            head = rows[0]
            stem, ext = os.path.splitext(path)
            backup = f"{stem}-old-{time.strftime('%m%d-%H%M%S')}{ext}"
            if "time" not in head:  # 看着不像自测记录：改名留着，新开一个
                os.replace(path, backup)
                say(f"{os.path.basename(path)} 看着不像自测记录，已改名为 {os.path.basename(backup)}，新开一个。")
                rows = []
            else:
                dropped = [c for c in head if c not in SELFTEST_COLS]
                if dropped:
                    shutil.copyfile(path, backup)
                    say(f"之前的自测记录里有 {len(dropped)} 列新版本不再用了，整个旧文件先原样备份成 "
                        f"{os.path.basename(backup)}")
                # 先写临时文件再替换，中途出错原文件也不会坏
                rows = [SELFTEST_COLS] + [[dict(zip(head, r)).get(k, "") for k in SELFTEST_COLS] for r in rows[1:]]
                with open(path + ".tmp", "w", newline="") as f:
                    csv.writer(f).writerows(rows)
                os.replace(path + ".tmp", path)
                say("之前的自测记录换成了新格式，角度这些数都留着，接着跟以前比。")
    prev = dict(zip(SELFTEST_COLS, rows[-1])) if len(rows) > 1 else None
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if not rows:
            w.writerow(SELFTEST_COLS)
        w.writerow([row.get(k, "") for k in SELFTEST_COLS])
    return prev


def print_selftest_report(res, feel, notes, prev):
    for line in selftest_report(res, feel, notes, prev):
        print(line)


def selftest_report(res, feel, notes, prev):
    """自测结果的文字版，一行一个字符串：终端里打出来；应用模式下还存进“自测结果.txt”。"""
    out = ["", "自测结果（只跟你自己比，不是诊断）："]
    out += ["  " + line for line in selftest_lines(res)]
    if feel:
        out.append("  第二轮做动作时的感觉（0–10，左右是你自己的左右）：")
        out += [f"    {big}：{feel_text(s, feel[s])}" for s, big, *_ in SELFTEST_STEPS if s in feel]
        if any(not feel_empty(v) for s in ("tilt_l", "tilt_r", "turn_l", "turn_r")
               for v in (feel.get(s) or {}).values() if v is not None):
            out.append("  对照（一边被拉长时另一边被挤）：")
            out += ["    " + line for line in feel_compare(feel)]
        change = feel_change_text(feel)
        if change:
            out.append("  " + change)
    out += ["  注意：" + n for n in notes]
    if not prev:
        return out
    num = lambda s: float(s) if s not in ("", None) else None
    lines = []
    for k in SELFTEST_KEYS:
        a, b = num(prev.get(k)), res.get(k)
        if a is not None and b is not None:
            name, unit = SELFTEST_NAMES[k]
            lines.append(f"{name}：{a:.0f} → {b:.0f}{unit}（{b - a:+.0f}）")
    for s in FEEL_STEPS:
        for q in FEEL_Q[s]:
            a, b = prev.get(f"{s}_{q.key}"), (feel.get(s) or {}).get(q.key)
            if a in ("", None) or b is None:
                continue
            a = int(float(a)) if q.kind == "num" else a
            if a != b:
                when = "做完" if s == "end" else f"{STEP_BIG[s]}时"
                lines.append(f"{when}{q.short}：{q.label(a)} → {q.label(b)}")
    out.append(f"  和上次（{prev.get('local_time', '?')}）比：")
    out += ["    " + line for line in lines or ["没有能比的数"]]
    return out


class SelfTest:
    """颈肩自测的流程，不卡住主循环：每圈调 update，每识别完一次调 feed，有按键调 key，窗口里的字用 screen。"""

    def __init__(self, path, now, report=None):
        self.path = path
        self.report = report            # 应用模式：结果的文字版另存一份（没有终端可看）
        self.once = False               # 做完就退出程序（--once），结果页的字跟着变
        self.neutral, self.reps = None, []
        self.feel = {}                  # 最后一轮每个动作（和最后的 "end"）：{"pain_long": 3, ...}，没答的是 None
        self.i, self.fi, self.typed = 0, 0, ""   # 第几个动作（两轮连着数）、正在答第几题、正在输入的数字
        self.feel_step, self.asked = None, []     # 正在答的是哪个动作（或 "end"）；用户自己答过的题号（退格用）
        self.last_v = self.start = None
        self.no_face = False
        self.result, self.saved, self.done = None, False, False
        self._phase("intro", now)
        print("\n颈肩自测：看预览窗口里的大字，跟着做。第二轮每个动作做完，会问你几道感觉题。按 Esc 随时停止。")
        if cn_font(40) is None:
            print("没找到中文字体，窗口里改用英文提示。可以用 --font 指定一个中文字体文件。")

    def _phase(self, phase, now):
        self.phase, self.t, self.samples = phase, now, []

    @property
    def step(self):
        return SELFTEST_STEPS[self.i % len(SELFTEST_STEPS)]

    @property
    def round(self):
        return self.i // len(SELFTEST_STEPS) + 1

    def feed(self, now, m):
        """每识别完一次调用，m 是 compute_metrics 的结果（看不到人时是 None）。"""
        if self.phase == "neutral":
            self.no_face = m is None
            if m is not None and m.get("head_roll_deg") is not None and now - self.t >= ST_SETTLE:
                self.samples.append(m)
        elif self.phase in ("ready", "go"):
            self.last_v = selftest_sample(st_kind(self.step[0]), m, self.neutral)
            if self.last_v is not None:
                self.samples.append((now, self.last_v))

    def update(self, now):
        el = now - self.t
        if self.phase == "neutral" and el >= ST_SETTLE + ST_NEUTRAL:
            if len(self.samples) >= 5:
                self.neutral = median_of(self.samples, METRIC_KEYS + SELFTEST_EXTRA)
                if self.neutral["shoulder_width"] is None:
                    print("没看到两边肩膀：这次耸肩测不了，歪头只看头。往后坐一点或者把屏幕往后仰，再测会更全。")
                self._phase("ready", now)
            else:
                print("看不清你的脸，重新来：坐到摄像头正前方。")
                self._phase("neutral", now)
        elif self.phase == "ready" and el >= ST_READY:
            kind = st_kind(self.step[0])
            recent = [selftest_mag(kind, v) for t, v in self.samples if now - t <= 1.5]
            self.start = statistics.median(recent) if recent else None  # 开始前离起点多远，留着查坐姿有没有漂
            self._phase("go", now)
        elif self.phase == "go" and (el >= ST_GO_MAX or (el >= ST_GO_MIN and self._came_back())):
            self._end_rep(timed_out=el >= ST_GO_MAX)
            if self.round == SELFTEST_ROUNDS:  # 最后一轮：每个动作做完马上问感觉
                self._begin_feel(self.step[0], now)
            else:
                self._next(now)
        elif self.phase == "result" and el >= ST_RESULT:
            self.done = True

    def _next(self, now):
        self.i += 1
        if self.i < len(SELFTEST_STEPS) * SELFTEST_ROUNDS:
            self._phase("ready", now)
        else:
            self.finish(now)

    def _came_back(self):
        """动到过位，现在又回到最大幅度的三分之一左右以内了。"""
        kind = st_kind(self.step[0])
        mags = [selftest_mag(kind, v) for _, v in self.samples]
        if len(mags) < 4:
            return False
        smooth = [statistics.median(mags[max(0, j - 2):j + 1]) for j in range(len(mags))]
        peak = max(smooth)
        return peak >= ST_MOVE_MIN[kind] and smooth[-1] < 0.35 * peak

    def _end_rep(self, timed_out):
        step, big = self.step[:2]
        kind = st_kind(step)
        v = selftest_peak(kind, [v for _, v in self.samples])
        if v is not None and selftest_mag(kind, v) < ST_MOVE_MIN[kind]:
            v = None
        self.reps.append(dict(step=step, round=self.round, v=v, start=self.start,
                              n=len(self.samples), end="timeout" if timed_out else "back"))
        print(f"  第 {self.round} 轮 {big}：" + (amount_text(kind, v) if v is not None else "没测到（看不清，或者没动）"))

    def key(self, k, now):
        """Esc 或 t：停下（第一轮做完了，已经做的先存上）。
        回答时：输数字按回车填一个（不填是 0），空格把剩下的都填 0，退格改。"""
        if k == 255:
            return
        if self.phase == "result":
            if now - self.t >= 1.5:  # 刚显示时的按键不算，免得一闪而过
                self.done = True
        elif k in (27, ord("t")):
            self.stop(now)
        elif self.phase == "intro" and k in (32, 13, 10):
            self._phase("neutral", now)
        elif self.phase == "feel":
            self._feel_key(k, now)

    # ----- 感觉题 -----
    def _begin_feel(self, step, now):
        """开始问一个动作（或最后的 "end"）的题。"""
        self.feel_step, self.feel[step] = step, {q.key: None for q in FEEL_Q[step]}
        self.fi, self.typed, self.asked = 0, "", []
        self._phase("feel", now)
        self._skip_conditional()

    @property
    def question(self):
        qs = FEEL_Q[self.feel_step]
        return qs[self.fi] if self.fi < len(qs) else None

    def _skip_conditional(self):
        """前一题是“没有”的话，跟着的那题不问，直接填默认值。"""
        ans = self.feel[self.feel_step]
        while self.question is not None and self.question.cond:
            prev_key, default = self.question.cond
            if not feel_empty(ans.get(prev_key)):
                break
            ans[self.question.key] = default
            self.fi += 1

    def _commit(self, v, now, rest=False):
        """填好这一题；rest = 剩下的都填“没有”。"""
        ans = self.feel[self.feel_step]
        ans[self.question.key] = v
        self.asked.append(self.fi)
        self.fi, self.typed = self.fi + 1, ""
        if rest:
            for q in FEEL_Q[self.feel_step][self.fi:]:
                ans[q.key] = self._none_of(q)
            self.fi = len(FEEL_Q[self.feel_step])
        self._skip_conditional()
        if self.question is None:
            self._feel_done(now)

    @staticmethod
    def _none_of(q):
        """这道题“没有”是什么值。"""
        if q.kind == "num":
            return 0
        if q.cond:
            return q.cond[1]
        return next((code for code, *_ in q.opts if code in ("none", "same")), None)

    def _feel_done(self, now):
        step = self.feel_step
        if step == "end":
            if not APP:  # 应用模式下打印的字进“运行记录.txt”，感觉的分数不放进去（见 finish）
                print(f"    {feel_change_text(self.feel) or '做完没答'}")
            self.finish(now)
            return
        if not APP:
            print(f"    做的时候：{feel_text(step, self.feel[step])}")
        if self.i + 1 >= len(SELFTEST_STEPS) * SELFTEST_ROUNDS:  # 最后一个动作：再问一句跟开始比怎么样
            self._begin_feel("end", now)
        else:
            self._next(now)

    def _feel_key(self, k, now):
        q, ans = self.question, self.feel[self.feel_step]
        if q is None:
            return
        if ord("0") <= k <= ord("9"):
            if q.kind == "choice":  # 选项按数字直接选
                j = int(chr(k)) - q.start
                if 0 <= j < len(q.opts):
                    self._commit(q.opts[j][0], now)
                return
            t = str(int(self.typed + chr(k)))
            self.typed = t if int(t) <= 10 else chr(k)
        elif k in (8, 127):  # 退格：删一位；已经删空了就回到上一题（自动填的题跳过）
            if self.typed:
                self.typed = self.typed[:-1]
            elif self.asked:
                for q2 in FEEL_Q[self.feel_step][self.asked[-1]:]:
                    ans[q2.key] = None
                self.fi = self.asked.pop()
                self.typed = ""
        elif k in (13, 10, 32):  # 回车：填这一题（不填是 0）；空格：这一题填好，剩下的都是“没有”
            if q.kind == "choice":
                if k != 32:
                    return
                v = self._none_of(q)
            else:
                v = int(self.typed) if self.typed else 0
            self._commit(v, now, rest=(k == 32))

    def _feel_screen(self):
        """答题时窗口里的字：一次一题，上面是题目和肌肉提示，中间是输入框或选项，下面是已答的和按键说明。"""
        W, G, C, D = (255, 255, 255), (90, 235, 90), (255, 225, 140), (200, 200, 200)
        step, q, ans = self.feel_step, self.question, self.feel[self.feel_step]
        if step == "end":
            head = ("全部做完了", "All done", 0.06, W)
        else:
            head = (f"第 {self.round} 轮　刚才“{STEP_BIG[step]}”的时候", f"Just now: {STEP_EN[step]}", 0.06, W)
        if q is None:
            return [head]
        rows = [head, (q.cn, q.en, 0.08, W)]
        if q.hint:
            rows.append((q.hint, "", 0.04, D))
        if q.compare:
            s2, k2, cn2, en2 = q.compare
            v2 = (self.feel.get(s2) or {}).get(k2)
            if v2 is not None:
                rows.append((f"{cn2} {v2}", f"{en2} {v2}", 0.05, C))
        rows.append(("", "", 0.02, W))
        if q.kind == "num":
            rows += [(f"［{self.typed}_］", f"[{self.typed}_]", 0.1, G),
                     ("0 = 没有，10 = 最重", "0 = none, 10 = worst", 0.045, D)]
        else:
            opts = "　".join(f"{q.start + j} {c}" for j, (code, c, e) in enumerate(q.opts))
            opts_en = "   ".join(f"{q.start + j} {e}" for j, (code, c, e) in enumerate(q.opts))
            rows.append((opts, opts_en, 0.06 if len(q.opts) <= 4 else 0.045, G))
        done = [(FEEL_Q[step][j].short + " " + FEEL_Q[step][j].label(ans[FEEL_Q[step][j].key]),
                 FEEL_Q[step][j].short_en + " " + FEEL_Q[step][j].label(ans[FEEL_Q[step][j].key], cn=False))
                for j in self.asked]
        if done:
            rows.append(("已答：" + "　".join(cn for cn, _ in done), "Answered: " + ", ".join(en for _, en in done),
                         0.04, D))
        rows.append(("", "", 0.02, W))
        if q.kind == "num":
            rows += [("输数字按回车，不填直接回车就是 0", "Type 0-10 + Enter (Enter alone = 0)", 0.05, G),
                     ("空格：这个动作剩下的都没有　　退格：改", "SPACE: rest are none    BACKSPACE: fix", 0.045, D)]
        else:
            rows += [("按数字选", "Press the number", 0.05, G),
                     ("空格：没有　　退格：改", "SPACE: none    BACKSPACE: fix", 0.045, D)]
        return rows

    def stop(self, now):
        """中途停下：第一轮做完了就把已经做的存上，并记下在哪停的；第一轮没做完就不存。"""
        if self.i >= len(SELFTEST_STEPS) and not self.saved:
            self.finish(now, stopped=True)
        else:
            say("自测停了，第一轮没做完，这次没保存。")
            self.done = True

    def finish(self, now, stopped=False):
        """存一行，终端里打出结果和上次的对比，窗口显示结果页。"""
        res, notes, codes = summarize_selftest(self.reps)
        stop_at = None
        if stopped:
            step, big = self.step[:2]
            if self.phase == "feel" and self.feel_step == "end":
                step, big = "end", "全部做完"
            stop_at = {"step": step, "round": self.round, "phase": self.phase}
            codes.insert(0, f"stopped_{step}_{self.round}")
            when = {"ready": "开始前", "go": "做的时候"}.get(self.phase, "做完回答时")
            notes.insert(0, f"第 {self.round} 轮“{big}”{when}停了，后面的没做")
        row = {"time": f"{now:.0f}", "local_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
               "warnings": ";".join(codes), "algo_version": algo_version(), "source": SOURCE}
        row.update({k: "" if res[k] is None else f"{res[k]:.1f}" for k in SELFTEST_KEYS})
        for s, ans in self.feel.items():
            row.update({f"{s}_{key}": "" if v is None else str(v) for key, v in ans.items()})
        rnd = lambda v: None if v is None else ([round(x, 4) for x in v] if isinstance(v, tuple) else round(v, 4))
        row["raw"] = json.dumps({
            "turn_k": TURN_K, "pitch_k": PITCH_K,
            "tilt_vs_shoulders": self.neutral.get("shoulder_tilt_deg") is not None,
            "stopped": stop_at,
            "neutral": {k: rnd(v) for k, v in self.neutral.items()},
            "reps": [dict(r, v=rnd(r["v"]), start=rnd(r["start"])) for r in self.reps]},
            separators=(",", ":"))
        prev = save_selftest(self.path, row)
        lines = selftest_report(res, self.feel, notes, prev)
        # 应用模式下打印的字都进“运行记录.txt”，出了问题会整个发给 gui。疼痛、拉扯这些分数算健康信息，
        # 要用户单独同意才给 gui（见 export_data），所以运行记录里只写角度；带分数的全文在“自测结果.txt”
        for line in selftest_report(res, {}, notes, prev) if APP else lines:
            print(line)
        print(f"已存到 {self.path}")
        if self.report:  # 新的写在最后，带上日期
            with open(self.report, "a", encoding="utf-8") as f:
                f.write(f"\n===== {row['local_time']} =====\n" + "\n".join(lines[1:]) + "\n")
        self.result, self.saved = (res, notes), True
        self._phase("result", now)

    def on_quit(self, now):
        """按 q 退出程序时：第一轮做完了就先存上，没答的空着。"""
        if self.phase != "result":
            self.stop(now)

    def screen(self, now):
        """窗口里要写的字：[(中文, 英文, 字高占画面高的比例, 颜色 BGR)]"""
        W, G, Y, C, D = (255, 255, 255), (90, 235, 90), (0, 215, 255), (255, 225, 140), (200, 200, 200)
        el = now - self.t
        if self.phase == "intro":
            return [("颈肩自测", "Neck & shoulder self-test", 0.11, W),
                    ("跟着做 7 个动作，左右交替，做两轮，约 3 分钟", "7 moves, 2 rounds, about 3 minutes", 0.05, W),
                    ("每个动作做到舒服的最大，停一下，再回正", "Go as far as comfortable, pause, come back", 0.05, W),
                    ("第二轮每个动作做完，问几道疼、拉扯感的题", "Round 2: a few pain / stretch questions per move",
                     0.05, W),
                    ("别硬拉。疼、头晕、手麻就按 Esc 停下", "Don't force it. Pain, dizzy or numb: press Esc", 0.05, Y),
                    ("左右都是你自己的左右", "Left / right = YOUR left / right", 0.045, D),
                    ("", "", 0.03, W),
                    ("坐正、看屏幕，按空格开始", "Sit upright, look here, press SPACE", 0.07, G)]
        if self.phase == "neutral":
            rows = [("坐正，看屏幕，别动", "Sit upright, look here, hold still", 0.09, W),
                    (str(max(1, math.ceil(ST_SETTLE + ST_NEUTRAL - el))),) * 2 + (0.18, G)]
            if self.no_face:
                rows.append(("看不到脸：坐到摄像头正前方", "Can't see your face", 0.055, Y))
            return rows
        if self.phase in ("ready", "go"):
            step, big, small, en = self.step
            n, k = len(SELFTEST_STEPS), self.i % len(SELFTEST_STEPS) + 1
            rows = [(f"第 {self.round} 轮　{k} / {n}", f"Round {self.round}  {k}/{n}", 0.045, D),
                    (big, en, 0.15, W if self.phase == "ready" else G),
                    (small, "", 0.055, W)]
            if self.phase == "ready":
                if self.round == SELFTEST_ROUNDS:
                    rows.append(("这一轮做的时候留意：哪里疼、哪里有拉扯感", "Notice where it hurts or pulls",
                                 0.05, C))
                rows.append((str(max(1, math.ceil(ST_READY - el))),) * 2 + (0.15, W))
            else:
                kind = st_kind(step)
                rows += [("开始！到最大停一下，再回正", "GO! Pause at your max, then come back", 0.07, G),
                         (amount_text(kind, self.last_v), amount_text(kind, self.last_v, cn=False), 0.07, C)]
            return rows
        if self.phase == "feel":
            return self._feel_screen()
        if self.phase == "result":
            res, notes = self.result
            rows = [("自测结果（只跟你自己比，不是诊断）", "Self-test result (compare with yourself only)", 0.06, W)]
            rows += [(cn, en, 0.055, W) for cn, en in zip(selftest_lines(res), selftest_lines(res, cn=False))]
            felt = [(f"{big}：{feel_text(s, self.feel[s])}", f"{s}: {feel_text(s, self.feel[s], cn=False)}")
                    for s, big, *_ in SELFTEST_STEPS
                    if any(v is not None and not feel_empty(v) for v in (self.feel.get(s) or {}).values())]
            if felt:
                rows.append(("做动作时（0–10）", "While moving (0-10)", 0.045, C))
                rows += [(cn, en, 0.045, C) for cn, en in felt[:4]]
                if len(felt) > 4:
                    where = f"“{os.path.basename(self.report)}”" if self.report else "终端"
                    rows.append((f"还有 {len(felt) - 4} 个动作有感觉，见{where}",
                                 f"{len(felt) - 4} more in the {'report file' if self.report else 'terminal'}",
                                 0.04, D))
            elif self.feel and all(v is None or feel_empty(v) for s, a in self.feel.items() if s != "end"
                                   for v in a.values()):
                rows.append(("做动作时哪里都不疼、没有拉扯感", "No pain or stretch while moving", 0.045, C))
            change = feel_change_text(self.feel)
            if change:
                rows.append((change, feel_change_text(self.feel, cn=False), 0.045, C))
            rows += [(n, "", 0.04, Y) for n in notes[:3]]
            if self.report:  # 应用模式没有终端
                saved = (f"已存好。详细结果和跟上次的对比在“{os.path.basename(self.report)}”，按 O 打开数据文件夹",
                         "Saved. Details in the report file (press O to open the data folder)")
            else:
                saved = (f"已存到 {os.path.basename(self.path)}，和上次比的变化在终端里",
                         f"Saved to {os.path.basename(self.path)}")
            back = ("按任意键结束", "Press any key to finish") if self.once else \
                ("按任意键回到监测", "Press any key to go back")
            rows += [("", "", 0.02, W), saved + (0.04, D), back + (0.055, G)]
            return rows
        return []


# ---------- 显示 ----------
def issue_side(k, d):
    """歪头、扭头、高低肩往哪边：left / right，其他问题或分不出是空字符串。高低肩说的是哪边肩膀高。"""
    key = {"head_tilt": "tilt_to_left_deg", "head_turn": "turn_to_left", "uneven_shoulders": "left_shoulder_up_deg"}
    v = d.get(key[k]) if k in key else None
    return "" if not v else "left" if v > 0 else "right"


def side_name(k, side):
    """带方向的中文名，比如“头往左歪”“右肩高”。side 是 left / right；分不出方向就是原来的名字。"""
    s = {"left": "左", "right": "右"}.get(side)
    names = {"head_tilt": f"头往{s}歪", "head_turn": f"头往{s}转", "uneven_shoulders": f"{s}肩高"}
    return names[k] if s and k in names else NAMES[k]


def issue_name(k, d):
    return side_name(k, issue_side(k, d))


def issue_text_cn(issues, d):
    if not issues:
        return "正常"
    parts = []
    for k in issues:
        extra = "、".join(FORWARD_BY[x] for x in d["forward_by"]) if k == "forward_head" else ""
        parts.append(issue_name(k, d) + (f"（{extra}）" if extra else ""))
    return "、".join(parts)


def status_lines(issues, sev, d, shares, a, sit_min, label, infer_ms):
    # OpenCV 不能画中文，窗口里用英文；L/R 是你自己的左右
    pct = lambda v: "n/a" if v is None else f"{v * 100:+.0f}%"
    num = lambda v: "n/a" if v is None else f"{v:+.2f}"
    sdeg = lambda v: "n/a" if v is None else f"{v:+.0f} deg"
    neg = lambda v: None if v is None else -v

    def lr(v, f, unit="", left=" L", right=" R"):  # 带方向的数：大小加上往哪边
        if v is None:
            return "n/a"
        return f"{abs(v):{f}}{unit}" + (left if v > 0 else right if v < 0 else "")
    head = ", ".join(k + (f"({'+'.join(d['forward_by'])})" if k == "forward_head" else "") for k in issues)
    if d["ear_sh_l_drop"] is not None:
        neck = f"ear-shoulder L {pct(neg(d['ear_sh_l_drop']))} R {pct(neg(d['ear_sh_r_drop']))}"
    else:
        neck = f"eye-shoulder {pct(neg(d['neck_drop']))}"
    busy = [f"{SHORT[k]} {shares[k]:.0%}" for k in NAMES if shares[k] > 0]
    return [
        ("OK" if not issues else "ISSUE: " + head) + f"  sev={sev:.2f}",
        f"fwd: head size {pct(d['head_growth'])} (>+{TH['forward_head_growth'] * 100:.0f}%)"
        f"  nose angle {sdeg(d['nose_angle_delta'])} (>+{TH['forward_head_nose_deg']:.0f})",
        f"down: eye-ear {num(d['head_pitch_delta'])} (<-{TH['head_down_pitch']:.2f})"
        f"  turn {lr(d['turn_to_left'], '.2f')} (>{TH['head_turn']:.2f})",
        f"shrug: up {pct(d['shoulder_rise'])} (>+{TH['shrug_rise'] * 100:.0f}%)"
        f"  {neck} (<-{TH['shrug_neck_drop'] * 100:.0f}%)",
        f"tilt: head {lr(d['tilt_to_left_deg'], '.0f', ' deg')} (>{TH['head_roll_deg']:.0f})"
        f"  shoulders {lr(d['left_shoulder_up_deg'], '.0f', ' deg', ' L up', ' R up')}"
        f" (>{TH['shoulder_tilt_deg']:.0f})",
        f"last {int(a.alert_after)}s: " + (" ".join(busy) if busy else "all OK") + f"  (alert at {a.share:.0%})",
        f"sitting {sit_min:.0f} min (alert at {a.sit_limit:.0f})",
        f"label={label}  infer {infer_ms:.0f} ms",
    ]


# ---------- 找点：两种跑模型的办法，结果一样 ----------
def count_persons(xyxy, i):
    """画面里有几个人：最大的框算一个，其他框的中心落在最大框外面才算另一个人。
    半躺时模型会把同一个人认成两个框（gui 10-27 滑坐时有 5~25 秒），那种框的中心在大框里面，不算。"""
    x0, y0, x1, y1 = xyxy[i]
    n = 1
    for j, (a0, b0, a1, b1) in enumerate(xyxy):
        if j != i and not (x0 <= (a0 + a1) / 2 <= x1 and y0 <= (b0 + b1) / 2 <= y1):
            n += 1
    return n


def pick_person(result):
    """返回 (17 个点, 17 个可信度, 画面里有几个人)。多个人时取框最大的那个（离摄像头最近），
    但主循环看到人数大于 1 会暂停判断，不用这些点。"""
    kp = result.keypoints
    if kp is None or kp.xy is None or len(kp.xy) == 0 or kp.conf is None:
        return None, None, 0
    boxes = result.boxes.xywh.cpu().numpy()
    i = int(max(range(len(boxes)), key=lambda j: boxes[j][2] * boxes[j][3]))
    return kp.xy[i].cpu().numpy(), kp.conf[i].cpu().numpy(), count_persons(result.boxes.xyxy.cpu().numpy(), i)


def bundled(name):
    """跟程序放在一起的文件（模型）：打包成应用时在应用里面，没打包时在这个脚本旁边。"""
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), name)


def load_detector(path):
    """.onnx 用 onnxruntime 跑（不用装 PyTorch，打包成应用用这个）；其他（.pt）用 Ultralytics 跑。
    返回一个函数：给一帧画面，返回 (17 个点的像素坐标, 17 个可信度, 画面里的人数)，看不到人时是 (None, None, 0)。
    旧的只返回两项的函数（测试里的假摄像头）也能用，人数按有没有点算。"""
    if path.lower().endswith(".onnx"):
        return OnnxPose(path)
    if YOLO is None:
        raise RuntimeError("用 .pt 模型要先装 ultralytics（pip install ultralytics），或者改用 --model yolo11n-pose.onnx")
    model = YOLO(path)  # 第一次运行会自动下载约 6 MB 的模型文件
    return lambda frame: pick_person(model(frame, verbose=False)[0])


def nms(boxes, scores, iou):
    """去掉重叠的框，和 torchvision.ops.nms 一样：按分数从高到低留，和已经留下的框重叠超过 iou 的扔掉。"""
    x1, y1, x2, y2 = boxes.T
    area = (x2 - x1) * (y2 - y1)
    order, keep = scores.argsort()[::-1], []
    while order.size:
        i, rest = order[0], order[1:]
        keep.append(i)
        w = np.maximum(0, np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]))
        h = np.maximum(0, np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]))
        inter = w * h
        order = rest[inter / (area[i] + area[rest] - inter + 1e-9) <= iou]
    return keep


class OnnxPose:
    """用 onnxruntime 跑导出成 .onnx 的 YOLO11n-pose。不用装 PyTorch（约 1 GB），打包成应用小很多。
    前后处理照搬 Ultralytics 8.4 用 .pt 模型时的做法：等比缩到 640，只补灰边到 32 的倍数，按 0.25 的分数和
    0.7 的重叠去框，再把坐标换回原图。所以找到的点和用 .pt 时一样，只差浮点误差（对比见 sim/compare_onnx.py）。
    模型要用 dynamic=True 导出（输入大小可变）；固定 640x640 的也能用，但要补成正方形，结果会差一点点。"""
    STRIDE = 32

    def __init__(self, path, imgsz=640, conf=0.25, iou=0.7):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.sess = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
        inp = self.sess.get_inputs()[0]
        self.name = inp.name
        self.square = all(isinstance(s, int) for s in inp.shape[2:])  # 固定大小的模型只能补成正方形
        self.imgsz, self.conf, self.iou = imgsz, conf, iou

    def letterbox(self, img):
        h0, w0 = img.shape[:2]
        r = min(self.imgsz / h0, self.imgsz / w0)
        new_w, new_h = max(round(w0 * r), 1), max(round(h0 * r), 1)
        dw, dh = self.imgsz - new_w, self.imgsz - new_h
        if not self.square:
            dw, dh = dw % self.STRIDE, dh % self.STRIDE
        dw, dh = dw / 2, dh / 2
        if (w0, h0) != (new_w, new_h):
            img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        return cv2.copyMakeBorder(img, round(dh - 0.1), round(dh + 0.1), round(dw - 0.1), round(dw + 0.1),
                                  cv2.BORDER_CONSTANT, value=(114, 114, 114))

    def __call__(self, frame):
        h0, w0 = frame.shape[:2]
        img = self.letterbox(frame)
        h1, w1 = img.shape[:2]
        x = np.ascontiguousarray(img[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255  # BGR→RGB
        p = self.sess.run(None, {self.name: x})[0][0].T  # 每行一个候选：中心 x、y，宽，高，分数，17×(x, y, 可信度)
        p = p[p[:, 4] > self.conf]
        if not len(p):
            return None, None, 0
        xyxy = np.concatenate([p[:, :2] - p[:, 2:4] / 2, p[:, :2] + p[:, 2:4] / 2], 1)
        keep = nms(xyxy, p[:, 4], self.iou)
        p, xyxy = p[keep], xyxy[keep]
        # 换回原图坐标，和 Ultralytics 的 scale_boxes / scale_coords 一样
        gain = min(h1 / h0, w1 / w0)
        new_h, new_w = max(round(h0 * gain), 1), max(round(w0 * gain), 1)
        gy, gx = new_h / h0, new_w / w0
        px, py = round((w1 - new_w) / 2 - 0.1), round((h1 - new_h) / 2 - 0.1)
        box = (xyxy - [px, py, px, py]) / [gx, gy, gx, gy]
        box[:, [0, 2]] = box[:, [0, 2]].clip(0, w0)
        box[:, [1, 3]] = box[:, [1, 3]].clip(0, h0)
        i = int(np.argmax((box[:, 2] - box[:, 0]) * (box[:, 3] - box[:, 1])))  # 框最大的那个人（离摄像头最近）
        k = p[i, 5:].reshape(17, 3)
        xy = np.stack([((k[:, 0] - px) / gx).clip(0, w0), ((k[:, 1] - py) / gy).clip(0, h0)], 1)
        return xy, k[:, 2].copy(), count_persons(box, i)


def notify(text):
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "体态提醒"'],
                       check=False, capture_output=True)


def draw_text(img, lines, color=(255, 255, 255), y=30, scale=0.7):
    step = int(43 * scale)
    for line in lines:
        cv2.putText(img, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(img, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)
        y += step


# OpenCV 自己只会写英文，中文大字要用 Pillow 和 Mac 自带的中文字体来画
FONT_FILES = ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
              "/System/Library/Fonts/STHeiti Light.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
              "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"]
_fonts = {}


def cn_font(px):
    """找一个能写中文的字体，找不到返回 None（这时改写英文）。"""
    if px not in _fonts:
        _fonts[px] = None
        try:
            from PIL import ImageFont
        except ImportError:
            return None
        for path in FONT_FILES:
            try:
                _fonts[px] = ImageFont.truetype(path, px)
                break
            except OSError:
                continue
    return _fonts[px]


def _text_w(text, px, cn):
    if cn:
        return cn_font(px).getlength(text)
    return cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, px / 30, max(2, px // 15))[0][0]


def _wrap(text, fits):
    """太长就断行：英文按词断，中文按字断。"""
    if not text:
        return []
    if fits(text):
        return [text]
    parts, sep = (text.split(" "), " ") if text.isascii() else (list(text), "")
    lines, cur = [], ""
    for p in parts:
        t = cur + sep + p if cur else p
        if cur and not fits(t):
            lines.append(cur)
            cur = p
        else:
            cur = t
    return lines + [cur]


def draw_big(img, rows):
    """在画面正中一行一行写大字。rows: [(中文, 英文, 字高占画面高的比例, 颜色 BGR)]。
    找得到中文字体就写中文，找不到就写英文。字太多放不下时整体缩小。"""
    h, w = img.shape[:2]
    cn = cn_font(max(10, int(h * 0.05))) is not None
    shrink = 1.0
    for _ in range(3):
        lines = []
        for text_cn, text_en, frac, color in rows:
            px = max(10, int(h * frac * shrink))
            fits = lambda t, px=px: _text_w(t, px, cn) <= 0.94 * w
            lines += [(t, px, color) for t in _wrap(text_cn if cn else text_en, fits)] or [("", px, color)]
        total = sum(int(px * 1.3) for _, px, _ in lines)
        if total <= 0.96 * h:
            break
        shrink *= 0.96 * h / total
    y = max(0, (h - total) // 2)
    if cn:
        import numpy as np
        from PIL import Image, ImageDraw
        pil = Image.fromarray(np.ascontiguousarray(img[:, :, ::-1]))
        draw = ImageDraw.Draw(pil)
    for text, px, color in lines:
        if text:
            x = int((w - _text_w(text, px, cn)) / 2)
            if cn:
                draw.text((x, y), text, font=cn_font(px), fill=color[::-1],
                          stroke_width=max(2, px // 14), stroke_fill=(0, 0, 0))
            else:
                # 黑边用错开几个像素的同样粗细的字画，不用更粗的字：新版 OpenCV 粗细不同字宽也不同，会对不齐
                scale, th, d = px / 30, max(2, px // 15), max(2, px // 20)
                for dx, dy in ((-d, 0), (d, 0), (0, -d), (0, d), (-d, -d), (d, d), (-d, d), (d, -d)):
                    cv2.putText(img, text, (x + dx, y + px + dy), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), th,
                                cv2.LINE_AA)
                cv2.putText(img, text, (x, y + px), cv2.FONT_HERSHEY_SIMPLEX, scale, color, th, cv2.LINE_AA)
        y += int(px * 1.3)
    if cn:
        img[:] = np.asarray(pil)[:, :, ::-1]


def open_csv(path, cols):
    """打开要一直往后加行的 CSV。之前版本写的列不一样，先改名留着，不覆盖。"""
    if os.path.exists(path):
        with open(path) as f:
            header = f.readline().strip().split(",")
        if header != cols:
            stem, ext = os.path.splitext(path)
            old = f"{stem}-old-{time.strftime('%m%d-%H%M%S')}{ext}"
            os.replace(path, old)
            print(f"之前的记录是旧版本写的，已改名为 {old}")
    new = not os.path.exists(path)
    f = open(path, "a", newline="")
    w = csv.writer(f)
    if new:
        w.writerow(cols)
    return f, w


def alerts_path(log_path):
    """提醒记录和日志放一起：log.csv → log-alerts.csv"""
    stem, ext = os.path.splitext(log_path)
    return f"{stem}-alerts{ext or '.csv'}"


# ---------- 应用模式：打包成 Mac 应用，不用终端 ----------
APP_NAME = "体态测试"
APP_DATA = "~/体态测试数据"      # 固定的数据文件夹；桌宠从这里读 log.csv、log-alerts.csv
RUNLOG = "运行记录.txt"          # 应用模式下本来打在终端里的字都写进这里，出了问题发这个
REPORT = "自测结果.txt"          # 每次自测的结果和跟上次的对比，给人看的
PIDFILE = ".running.pid"        # 正在运行的那个的进程号；同一时间只让一个在写数据
# 窗口的名字只用英文：OpenCV 在 Mac 上按系统编码（英文系统是 MacRoman）读窗口名，中文名在标题栏上是乱码，
# 关窗口时也找不到它。标题栏上的中文是窗口开了以后另外改的（mac_title）
WIN_APP = "Posture Test (video not saved)"
WIN_TITLE = "体态测试（画面不保存）"
APP_W = 960                     # 窗口宽度，画面等比缩到这么宽
CAMERA_SETTINGS = "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"
# 退出码，桌宠从命令行启动时用来判断为什么退出了。2 留给参数写错（argparse 自己用 2）
EXIT_ERROR, EXIT_RUNNING, EXIT_NO_CAMERA, EXIT_NO_BASELINE = 1, 3, 4, 5
WHITE, GREEN, YELLOW, CYAN, GREY = (255, 255, 255), (90, 235, 90), (0, 215, 255), (255, 225, 140), (200, 200, 200)


class RunLog:
    """应用模式下代替终端：每行带上时间写进“运行记录.txt”。在终端里启动时同时照常输出。"""

    def __init__(self, path, also=None):
        self.f = open(path, "a", encoding="utf-8", buffering=1)
        self.also, self.bol = also, True
        self.saved_fds = []

    def capture_native(self):
        """OpenCV、onnxruntime 不经过 Python、直接往 stdout/stderr 写的警告，也写进运行记录（不带时间）。
        在终端里启动时不动，照常打在终端里。被桌宠用管道启动、那边又不读时，写满了程序会卡住，这样也避免了。"""
        for fd, stream in ((1, sys.__stdout__), (2, sys.__stderr__)):
            if terminal(stream) is None:
                try:
                    stream.flush()  # 之前没写完的先写到原来的地方
                except (AttributeError, OSError, ValueError):
                    pass
                try:
                    saved = os.dup(fd)
                except OSError:
                    continue
                os.dup2(self.f.fileno(), fd)
                self.saved_fds.append((fd, saved))

    def close(self):
        self.f.flush()
        for fd, saved in self.saved_fds:
            os.dup2(saved, fd)
            os.close(saved)
        self.saved_fds = []
        self.f.close()

    def write(self, s):
        for part in s.splitlines(keepends=True):
            if self.bol and part.strip():
                self.f.write(time.strftime("%m-%d %H:%M:%S "))
            self.f.write(part)
            self.bol = part.endswith("\n")
        if self.also:
            try:
                self.also.write(s)
                self.also.flush()
            except (OSError, ValueError):
                self.also = None
        return len(s)

    def flush(self):
        self.f.flush()

    def isatty(self):
        return False


def terminal(stream):
    """在终端里启动（开发时）才同时往终端打。从 Finder 打开时输出接的是 /dev/null；
    被桌宠用管道启动时也不往管道写：没人读的话管道写满了程序会卡住。桌宠要看就读“运行记录.txt”。"""
    try:
        return stream if stream.isatty() else None
    except (AttributeError, OSError, ValueError):
        return None


def alert(msg):
    """弹一个对话框（应用模式没有终端，出了问题不弹就看不到）。"""
    if sys.platform == "darwin":
        t = msg.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display dialog "{t}" with title "{APP_NAME}" buttons {{"好"}} '
                        f'default button 1 with icon caution'], check=False, capture_output=True)


def fatal(msg, code=EXIT_ERROR, dialog=True):
    """出了问题要退出：打出来；应用模式下有窗口时再弹个对话框。"""
    print(msg)
    if APP and dialog:
        alert(msg)
    sys.exit(code)


def open_path(path):
    """在 Finder 里打开文件夹，或者打开系统设置的某一页。"""
    if sys.platform == "darwin":
        subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_LOCK = None


def take_lock(folder):
    """同一个数据文件夹同一时间只让一个在跑：桌宠在后台开着一个、又双击打开一个时，免得两个一起写记录。
    拿到了返回打开的文件（一直留着，程序退出时自动放开），里面写着进程号；没拿到返回 None。"""
    import fcntl
    f = open(os.path.join(folder, PIDFILE), "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.seek(0)
    f.truncate()
    f.write(f"{os.getpid()}\n")
    f.flush()
    global _LOCK
    _LOCK = f
    return f


def release_lock():
    global _LOCK
    if _LOCK:
        _LOCK.seek(0)
        _LOCK.truncate()  # 退出时清空进程号，免得桌宠读到一个已经不在的
        _LOCK.close()
        _LOCK = None


# ---------- 导出数据：把数据文件夹打成一个 zip，好发给 gui ----------
EXPORT_DIR = "导出"              # zip 放在数据文件夹里的这个文件夹。不放桌面：Mac 上应用写桌面要另外授权
ID_FILE = ".participant_id"     # 这个数据文件夹的匿名编号，第一次导出时生成
EXPORT_VERSION = 1              # zip 里的格式改了就加 1
BASELINE = "baseline.json"
# 只带认得的文件（和改名留着的旧版本），数据文件夹里别的东西（用户自己放进去的、导出过的 zip）都不带
EXPORT_LOGS = re.compile(r"^(log|log-alerts)(-old-\d{4}-\d{6})?\.csv$")
EXPORT_SELFTESTS = re.compile(r"^selftest(-old-\d{4}-\d{6})?\.csv$")
# 自测记录里不是感觉的列：时间、角度、提示、版本、raw。疼痛、拉扯、位置、“跟开始比”都不在里面；
# 以前版本的列只要不在这里也不带，免得漏掉旧版本问的分数
SELFTEST_SAFE = ["time", "local_time"] + SELFTEST_KEYS + ["warnings", "algo_version", "source", "raw"]
RAW_SAFE = {"turn_k", "pitch_k", "tilt_vs_shoulders", "stopped", "neutral", "reps"}  # raw 里只留这些
REP_SAFE = {"step", "round", "v", "start", "n", "end"}
# 以前的版本把自测时答的感觉也打进了运行记录：带这些字的行不带（现在的版本不往运行记录里写分数）
FEEL_WORDS = ("疼", "痛", "紧", "酸", "麻", "不适", "拉扯", "舒服", "感觉", "做的时候", "跟开始比", "做完没答",
              "pain", "tight", "stretch", "sore")
_STAMP = re.compile(r"^\d\d-\d\d \d\d:\d\d:\d\d ")  # 运行记录每行开头的时间


class ExportError(Exception):
    """导出不了，原因是给人看的一句话。"""


def participant_id(folder):
    """这个数据文件夹的匿名编号（8 位十六进制）：第一次导出时随机生成、存在文件夹里，以后一直用它。
    和名字、电脑都没关系；gui 靠它分清几份数据是不是同一个人的。"""
    path = os.path.join(folder, ID_FILE)
    try:
        with open(path) as f:
            pid = f.read().strip()
        if re.fullmatch(r"[0-9a-f]{8}", pid):
            return pid
    except OSError:
        pass
    pid = secrets.token_hex(4)
    with open(path + ".tmp", "w") as f:
        f.write(pid + "\n")
    os.replace(path + ".tmp", path)
    return pid


def _complete_lines(path):
    """一行一行读成文字。最后一行没有换行就不要：程序可能正开着，那行还没写完。"""
    with open(path, "rb") as f:
        for line in f:
            if line.endswith(b"\n"):
                yield line.decode("utf-8", "replace")


def _zinfo(name, now):
    info = zipfile.ZipInfo(name, date_time=max(time.localtime(now)[:6], (1980, 1, 1, 0, 0, 0)))  # zip 不认 1980 年以前
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16  # 普通文件，rw-r--r--
    return info


def _zip_text(zf, info, src):
    """往 zip 里一边读一边写（一个月的记录有两百多兆，不整个读进内存）。
    原文件超过 1 GB 才用 zip64 格式（单个文件能超过 4 GB）；平常不用，老的解压工具也打得开。"""
    big = os.path.getsize(src) > 2 ** 30
    return io.TextIOWrapper(zf.open(info, "w", force_zip64=big), encoding="utf-8", newline="")


def _export_csv(zf, info, path, keep, fix=None):
    """把一个 CSV 抄进 zip：只要 keep(列名) 为真的列；fix = {列名: 改这一格的函数}。
    列数对不上的行（写到一半程序被关掉的）不要。返回写进 manifest 的情况；空文件返回 None，不放进 zip。"""
    rows = csv.reader(_complete_lines(path))
    head = next(rows, None)
    if not head:
        return None
    cols = [i for i, c in enumerate(head) if keep(c)]
    fixes = [(i, fix[c]) for i, c in enumerate(head) if fix and c in fix]
    n = bad = 0
    with _zip_text(zf, info, path) as out:
        w = csv.writer(out)
        w.writerow([head[i] for i in cols])
        for r in rows:
            if len(r) != len(head):
                bad += 1
                continue
            for i, f in fixes:
                r[i] = f(r[i])
            w.writerow([r[i] for i in cols])
            n += 1
    st = {"rows": n}
    if bad:
        st["dropped_rows"] = bad
    removed = [c for c in head if not keep(c)]
    if removed:
        st["removed_columns"] = removed
    return st


def _clean_raw(s):
    """自测记录 raw 那一格（JSON）：只留各次动作的数这些，别的不带（以前的版本可能放过感觉）。"""
    try:
        d = json.loads(s)
    except ValueError:
        return ""
    if not isinstance(d, dict):
        return ""
    reps = d.pop("reps", None)
    d = {k: v for k, v in d.items() if k in RAW_SAFE}
    if isinstance(reps, list):
        d["reps"] = [{k: v for k, v in r.items() if k in REP_SAFE} for r in reps if isinstance(r, dict)]
    return json.dumps(d, separators=(",", ":"))


def _clean_runlog(lines, homes):
    """运行记录导出前：个人文件夹的路径换成 ~（路径里有用户名）；以前的版本打进来的感觉去掉，
    整段“自测结果”也去掉（角度在 selftest.csv 里，不会少）。一行一行给出来，去掉的行给 None。"""
    pats = [re.compile(re.escape(h) + r"(?![\w.-])") for h in homes]
    report = False
    for line in lines:
        body = _STAMP.sub("", line, count=1)
        # 自测结果那段除了第一行都是缩进的。中间夹着的没有时间的行（OpenCV 自己写的警告）也算在里面
        if report and (body == line or not body.strip() or body.startswith((" ", "\t"))):
            yield None
            continue
        report = "自测结果（只跟你自己比" in body
        if report or any(w in body for w in FEEL_WORDS):
            yield None
            continue
        for p in pats:
            line = p.sub("~", line)
        yield line


def export_data(folder, with_pain=False, with_points=False, now=None):
    """把数据文件夹打成一个 zip，放在“文件夹/导出/”里，返回 (zip 的路径, manifest)。
    一定带：姿势记录（log.csv 和改名留着的旧版本）、提醒记录、自测的角度、校准结果、运行记录（用户名换成 ~）。
    with_pain（用户单独同意了才开）：自测时打的疼痛、拉扯分数、位置、“跟开始比”，和带着这些的“自测结果.txt”。
    with_points（用户单独同意了才开）：log.csv 里 7 个点的原始坐标。同意书草稿说，原始点要等伦理审查或律师
    说清楚算不算生物特征再收，所以也单独问。
    zip 里有 manifest.json：匿名编号、导出时间、带没带这两样、每个文件去掉了什么。"""
    now = time.time() if now is None else now
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        raise ExportError(f"没有找到数据文件夹 {folder}")
    names = sorted(os.listdir(folder))
    logs = [n for n in names if EXPORT_LOGS.match(n)]
    tests = [n for n in names if EXPORT_SELFTESTS.match(n)]
    if not (logs or tests or BASELINE in names or RUNLOG in names):
        raise ExportError("还没有记录可以导出：先校准，再坐一会儿")
    pid = participant_id(folder)
    out_dir = os.path.join(folder, EXPORT_DIR)
    os.makedirs(out_dir, exist_ok=True)
    stem = f"posture-data-{pid}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}"
    path, k = os.path.join(out_dir, stem + ".zip"), 2
    while os.path.exists(path):
        path, k = os.path.join(out_dir, f"{stem}-{k}.zip"), k + 1
    top = os.path.splitext(os.path.basename(path))[0]  # zip 里也套一层同名文件夹，解开几份不会混
    man = {"participant_id": pid, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
           "export_version": EXPORT_VERSION, "algo_version": algo_version(),
           "includes_pain_scores": bool(with_pain), "includes_raw_points": bool(with_points),
           "files": {}, "left_out": []}
    home = os.path.expanduser("~")
    homes = sorted({h for h in (home, os.path.realpath(home)) if len(h) > 1}, key=len, reverse=True)
    part = path + ".part"  # 写完了才改成 .zip，免得发出去一个没写完的
    try:
        with zipfile.ZipFile(part, "w") as zf:
            for n in logs:
                st = _export_csv(zf, _zinfo(f"{top}/{n}", now), os.path.join(folder, n),
                                 lambda c: with_points or c not in KP_COLS)
                if st:
                    man["files"][n] = st
            for n in tests:
                st = _export_csv(zf, _zinfo(f"{top}/{n}", now), os.path.join(folder, n),
                                 lambda c: with_pain or c in SELFTEST_SAFE, None if with_pain else {"raw": _clean_raw})
                if st:
                    man["files"][n] = st
            if BASELINE in names:
                zf.write(os.path.join(folder, BASELINE), f"{top}/{BASELINE}", zipfile.ZIP_DEFLATED)
                man["files"][BASELINE] = {}
            if RUNLOG in names:
                kept = removed = 0
                src = os.path.join(folder, RUNLOG)
                with _zip_text(zf, _zinfo(f"{top}/{RUNLOG}", now), src) as out:
                    for line in _clean_runlog(_complete_lines(src), homes):
                        if line is None:
                            removed += 1
                        else:
                            out.write(line)
                            kept += 1
                man["files"][RUNLOG] = {"lines": kept, "removed_lines": removed}
            if REPORT in names:
                if with_pain:
                    zf.write(os.path.join(folder, REPORT), f"{top}/{REPORT}", zipfile.ZIP_DEFLATED)
                    man["files"][REPORT] = {}
                else:
                    man["left_out"].append(REPORT)
            zf.writestr(_zinfo(f"{top}/manifest.json", now), json.dumps(man, ensure_ascii=False, indent=1) + "\n")
        os.replace(part, path)
    except BaseException:
        if os.path.exists(part):
            os.remove(part)
        raise
    return path, man


def export_summary(man, cn=True):
    """带没带那两样，一句话。"""
    if cn:
        return (f"疼痛、拉扯分数：{'带上了' if man['includes_pain_scores'] else '没带'}；"
                f"7 个点的坐标：{'带上了' if man['includes_raw_points'] else '没带'}")
    return (f"pain/stretch scores: {'included' if man['includes_pain_scores'] else 'left out'}; "
            f"keypoints: {'included' if man['includes_raw_points'] else 'left out'}")


def reveal(path):
    """在访达里打开文件所在的文件夹，并选中它。"""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def export_cli(a):
    """--export：不开摄像头、不开窗口，打好 zip 就退出。最后一行是 zip 的路径，给桌宠读。"""
    try:
        path, man = export_data(a.data_dir, a.with_pain, a.with_points)
    except (ExportError, OSError) as e:
        print(f"没导出：{e}")
        return EXIT_ERROR
    print(export_summary(man))
    print(path)
    return 0


def put_texts(img, items):
    """在指定位置写一批字。items: [(中文, 英文, x, y, 字高, 颜色 BGR, 对齐)]，y 是字的顶边，
    对齐 "l" 从 x 往右写、"c" 以 x 为中心、"r" 写到 x 为止。有中文字体写中文，没有写英文。"""
    if not items:
        return
    cn = cn_font(20) is not None
    if cn:
        from PIL import Image, ImageDraw
        pil = Image.fromarray(np.ascontiguousarray(img[:, :, ::-1]))
        draw = ImageDraw.Draw(pil)
    for t_cn, t_en, x, y, px, color, align in items:
        text = t_cn if cn else t_en
        if not text:
            continue
        w = _text_w(text, px, cn)
        x0 = int(x - w / 2 if align == "c" else x - w if align == "r" else x)
        if cn:
            draw.text((x0, int(y)), text, font=cn_font(px), fill=color[::-1],
                      stroke_width=max(1, px // 12), stroke_fill=(0, 0, 0))
        else:
            scale, th = px / 30, max(1, px // 15)
            cv2.putText(img, text, (x0, int(y + px)), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), th + 3, cv2.LINE_AA)
            cv2.putText(img, text, (x0, int(y + px)), cv2.FONT_HERSHEY_SIMPLEX, scale, color, th, cv2.LINE_AA)
    if cn:
        img[:] = np.asarray(pil)[:, :, ::-1]


def mac_title(win, title):
    """把 OpenCV 开的、标题是 win 的窗口，标题改成 title（可以是中文）。改成了返回 True。
    OpenCV 在 Mac 上按系统编码读标题（它自己的 setWindowTitle 也一样），中文会变乱码，所以直接调 Mac 的系统接口：
    Objective-C 运行库，用 Python 自带的 ctypes，不用装别的包。不是 Mac、或者哪一步不行，就不改，标题保持英文。"""
    if sys.platform != "darwin":
        return False
    try:
        import ctypes
        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")  # 系统自带，每台 Mac 都有
        for name, res, args in (("objc_getClass", ctypes.c_void_p, [ctypes.c_char_p]),
                                ("sel_registerName", ctypes.c_void_p, [ctypes.c_char_p]),
                                ("objc_autoreleasePoolPush", ctypes.c_void_p, []),
                                ("objc_autoreleasePoolPop", None, [ctypes.c_void_p])):
            f = getattr(objc, name)
            f.restype, f.argtypes = res, args
        send = objc.objc_msgSend

        def msg(obj, sel, restype=ctypes.c_void_p, *args):
            """给 obj 发消息 sel，args 是 (类型, 值)。Apple 芯片上 objc_msgSend 要按确切的类型调用，所以每次先设好。"""
            send.restype = restype
            send.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [t for t, _ in args]
            return send(obj, objc.sel_registerName(sel), *[v for _, v in args])

        pool = objc.objc_autoreleasePoolPush()  # 下面拿到的临时对象，用完一起释放
        try:
            windows = msg(msg(objc.objc_getClass(b"NSApplication"), b"sharedApplication"), b"windows")
            new = msg(objc.objc_getClass(b"NSString"), b"stringWithUTF8String:", ctypes.c_void_p,
                      (ctypes.c_char_p, title.encode("utf-8")))
            if not windows or not new:
                return False
            done = False
            for i in range(msg(windows, b"count", ctypes.c_ulong)):
                w = msg(windows, b"objectAtIndex:", ctypes.c_void_p, (ctypes.c_ulong, i))
                old = msg(w, b"title")
                if old and msg(old, b"UTF8String", ctypes.c_char_p) == win.encode("utf-8"):
                    msg(w, b"setTitle:", None, (ctypes.c_void_p, new))
                    done = True
            return done
        finally:
            objc.objc_autoreleasePoolPop(pool)
    except Exception:
        return False


class AppUI:
    """应用模式的窗口：最上面一行中文状态，中间提示，下面是最近几条提示和一排可以点的按钮。"""
    MAIN = [("校准（C）", "Calibrate (C)", ord("c")), ("颈肩自测（T）", "Self-test (T)", ord("t")),
            ("打开数据（O）", "Data (O)", ord("o")), ("导出数据（E）", "Export (E)", ord("e")),
            ("显示数字（D）", "Numbers (D)", ord("d")), ("退出（Q）", "Quit (Q)", ord("q"))]

    def __init__(self, win):
        self.win, self.rects, self.click = win, [], None
        self.details, self.seen = False, False
        self.banner, self.banner_until = None, 0.0
        cv2.namedWindow(win)
        cv2.setMouseCallback(win, self.on_mouse)
        self.titled, self.title_tries = mac_title(win, WIN_TITLE), 1

    def on_mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONUP:
            self.click = (x, y)

    def key(self, wait_ms):
        """等一下按键（顺便让窗口刷新）；点了按钮就当按了那个键；窗口被关掉就当按了 q。没有返回 255。"""
        k = cv2.waitKey(wait_ms) & 0xFF
        if not self.titled and self.title_tries < 10:  # 刚开窗口时没改成，等窗口出来再试几次
            self.titled, self.title_tries = mac_title(self.win, WIN_TITLE), self.title_tries + 1
        if k == 255 and self.click is not None:
            x, y = self.click
            self.click = None
            k = next((b for x0, y0, x1, y1, b in self.rects if x0 <= x <= x1 and y0 <= y <= y1), 255)
        if self.closed():
            k = ord("q")
        return k

    def closed(self):
        """窗口被关了：先看到过窗口开着，后来又没了。（拿不到这个属性的系统上就当没关）"""
        try:
            visible = cv2.getWindowProperty(self.win, cv2.WND_PROP_VISIBLE)
        except cv2.error:
            return False
        if visible >= 1:
            self.seen = True
            return False
        return self.seen

    def show_banner(self, text, now, secs=15):
        self.banner, self.banner_until = text, now + secs

    @staticmethod
    def bar_top(img):
        return img.shape[0] - max(34, int(img.shape[0] * 0.08))

    def bar(self, img, buttons, texts):
        """最下面一排按钮：[(中文, 英文, 按键)]。字先放进 texts，最后一起写。返回按钮条的上边。"""
        h, w = img.shape[:2]
        y0 = self.bar_top(img)
        bh = h - y0
        img[y0:] = img[y0:] // 4
        self.rects = []
        if not buttons:  # 比如正在打包的时候：没有能点的
            return y0
        gap, n = 8, len(buttons)
        bw = (w - gap * (n + 1)) / n
        px = max(13, int(bh * 0.4))
        for i, (cn, en, key) in enumerate(buttons):
            x0 = int(gap + i * (bw + gap))
            x1 = int(x0 + bw)
            cv2.rectangle(img, (x0, y0 + 5), (x1, h - 5), (70, 70, 70), -1)
            cv2.rectangle(img, (x0, y0 + 5), (x1, h - 5), (150, 150, 150), 1)
            texts.append((cn, en, (x0 + x1) / 2, y0 + (bh - px) / 2 - 2, px, WHITE, "c"))
            self.rects.append((x0, y0, x1, h, key))
        return y0

    def messages(self, img, bottom, now, texts):
        """按钮上面写最近 45 秒里的几条提示，太长就断行。"""
        h, w = img.shape[:2]
        px = max(13, int(h * 0.032))
        cn = cn_font(px) is not None
        lines = []
        for when, text in MSGS:
            if now - when < 45:
                lines += _wrap(text, lambda t: _text_w(t, px, cn) <= w - 30) if cn else [text]
        y = bottom - 8
        for text in reversed(lines[-4:]):
            y -= int(px * 1.35)
            texts.append((text, text if text.isascii() else "", 14, y, px, GREY, "l"))

    def draw_monitor(self, view, now, mode, status="", warn=False, note="", sit_min=0.0, details=(), calib_n=0):
        """平时监测时的窗口。mode: need_calib（还没校准）、calibrating（校准中）、monitor（在判断）。"""
        h, w = view.shape[:2]
        texts = []
        if mode == "need_calib":
            draw_big(view[:self.bar_top(view)], [("先校准一次", "Calibrate first", 0.1, WHITE),
                            ("坐正，看屏幕中间，然后按 C 或点下面的“校准”", "Sit upright, look here, then press C", 0.05, GREEN),
                            ("以后挪了电脑、换了座位，再校准一次", "Recalibrate after moving the laptop or chair", 0.04, GREY),
                            ("", "", 0.04, WHITE),
                            ("画面只在这台电脑上处理，不保存", "The video stays on this Mac and is never saved", 0.04, GREY),
                            ("只存数字，在“个人文件夹 / 体态测试数据”里", "Only numbers are saved, in ~/体态测试数据", 0.04, GREY)])
        elif mode == "calibrating":
            draw_big(view[:self.bar_top(view)], [("校准中：坐正，看屏幕中间，别动", "Calibrating: sit upright, hold still", 0.07, WHITE),
                            (f"{calib_n} / 5",) * 2 + (0.14, GREEN)])
        else:
            big = max(18, int(h * 0.058))
            texts.append((status, "", 14, 10, big, YELLOW if warn else GREEN, "l"))
            y = 10 + int(big * 1.35)
            small = max(13, int(h * 0.034))
            for line in ([note] if note else []) + [f"已经连续坐了 {sit_min:.0f} 分钟"]:
                texts.append((line, "", 14, y, small, GREY, "l"))
                y += int(small * 1.4)
            if self.details:  # 给开发看的英文数字
                draw_text(view, list(details), WHITE, y=y + 18, scale=0.5)
            if not cn_font(big):  # 没有中文字体时（不应该发生），至少写上英文状态
                draw_text(view, list(details[:1]), YELLOW if warn else WHITE)
        if self.banner and now < self.banner_until:  # 刚发的提醒，在窗口里也挂一会儿
            px = max(16, int(h * 0.05))
            cn = cn_font(px) is not None
            lines = _wrap(self.banner, lambda t: _text_w(t, px, cn) <= w * 0.86) if cn else ["Reminder"]
            top = int(h * 0.3)
            bottom = top + int(px * 1.4) * len(lines) + 24
            view[top:bottom, int(w * 0.05):int(w * 0.95)] //= 3
            cv2.rectangle(view, (int(w * 0.05), top), (int(w * 0.95), bottom), YELLOW, 2)
            for j, line in enumerate(lines):
                texts.append((line, "Reminder", w / 2, top + 12 + j * int(px * 1.4), px, YELLOW, "c"))
        y0 = self.bar(view, self.MAIN, texts)
        self.messages(view, y0, now, texts)
        put_texts(view, texts)

    def draw_selftest_bar(self, view, st):
        """自测时下面的按钮：每一步能点的不一样。"""
        if st.phase == "intro":
            b = [("开始（空格）", "Start (Space)", 32), ("停止（Esc）", "Stop (Esc)", 27)]
        elif st.phase == "feel":
            b = [("下一项（回车）", "Next (Enter)", 13), ("剩下都是 0（空格）", "Rest 0 (Space)", 32),
                 ("停止（Esc）", "Stop (Esc)", 27)]
        elif st.phase == "result":
            b = [("结束（回车）", "Done (Enter)", 13)] if st.once else [("回到监测（回车）", "Back (Enter)", 13)]
        else:
            b = [("停止自测（Esc）", "Stop (Esc)", 27)]
        texts = []
        self.bar(view, b, texts)
        put_texts(view, texts)

    def draw_export(self, view, ex):
        """导出数据时的窗口：问题或结果写在中间，下面是这一步能点的按钮。"""
        rows, buttons = ex.screen()
        draw_big(view[:self.bar_top(view)], rows)
        texts = []
        self.bar(view, buttons, texts)
        put_texts(view, texts)

    def camera_screen(self, waited):
        """打不开摄像头时的窗口：第一次要等人在系统弹窗里点允许。"""
        canvas = np.zeros((540, APP_W, 3), np.uint8)
        if waited < 2:
            rows = [("正在打开摄像头…", "Opening the camera...", 0.08, WHITE)]
        else:
            rows = [("等你允许使用摄像头", "Waiting for camera permission", 0.085, WHITE),
                    ("弹出“想访问摄像头”的窗口时，点“允许”", "When asked about the camera, click Allow", 0.045, GREEN),
                    ("", "", 0.03, WHITE),
                    ("之前点过“不允许”的话：点下面的“打开系统设置”，", "Clicked Don't Allow before? Open Settings,", 0.042,
                     GREY),
                    ("在“摄像头”里打开“体态测试”，再重新打开这个应用", "turn on 体态测试 under Camera, reopen this app",
                     0.042, GREY)]
        draw_big(canvas[:self.bar_top(canvas)], rows)
        texts = []
        self.bar(canvas, [("打开系统设置（S）", "Settings (S)", ord("s")), ("退出（Q）", "Quit (Q)", ord("q"))], texts)
        put_texts(canvas, texts)
        return canvas


class ExportAsk:
    """窗口里的“导出数据”：先问两个要单独同意的问题（疼痛分数、7 个点的坐标），再在后台打包
    （记录多的话要十几秒，不卡住窗口，判断照常），打好了在访达里选中那个 zip。"""
    YES_NO = [("带上（Y）", "Include (Y)", ord("y")), ("不带（N）", "Leave out (N)", ord("n")),
              ("取消（Esc）", "Cancel (Esc)", 27)]
    OK = [("好（回车）", "OK (Enter)", 13)]
    SHOW_S = 60  # 结果页放这么久没人按就自己关，回到监测

    def __init__(self, folder):
        self.folder, self.phase, self.ans = folder, "pain", {}
        self.thread = self.path = self.man = self.error = self.shown = None
        self.done = False

    def key(self, k, now):
        if 65 <= k <= 90:  # 开着大写锁定也认
            k += 32
        if self.phase in ("pain", "points"):
            if k == 27:
                say("没导出。")
                self.done = True
            elif k in (ord("y"), ord("n")):
                self.ans[self.phase] = k == ord("y")
                if self.phase == "pain":
                    self.phase = "points"
                else:
                    self.phase = "working"
                    self.thread = threading.Thread(target=self._work, name="export")
                    self.thread.start()
        elif self.phase in ("ok", "failed") and k in (13, 10, 32, 27):
            self.done = True

    def _work(self):
        try:
            self.path, self.man = export_data(self.folder, self.ans["pain"], self.ans["points"])
        except ExportError as e:
            self.error = str(e)
        except Exception as e:  # 比如磁盘满了
            traceback.print_exc()
            self.error = f"{type(e).__name__}: {e}"
        if self.error:
            say(f"没导出：{self.error}")
            self.phase = "failed"
        else:
            m = self.man  # 这句也进运行记录：不用“疼”这些字，下次导出时就不会被当成感觉去掉
            say(f"导出好了：{os.path.basename(self.path)}（分数{'带了' if m['includes_pain_scores'] else '没带'}，"
                f"坐标{'带了' if m['includes_raw_points'] else '没带'}）")
            reveal(self.path)
            self.phase = "ok"

    def update(self, now):
        if self.phase in ("ok", "failed"):
            if self.shown is None:
                self.shown = now
            elif now - self.shown >= self.SHOW_S:
                self.done = True

    def wait(self):
        """退出程序前等打包写完（不然写了一半的 zip 会留在那里）。"""
        if self.thread:
            self.thread.join()

    def screen(self):
        """窗口里要写的字 [(中文, 英文, 字高占画面高的比例, 颜色)]，和下面的按钮 [(中文, 英文, 按键)]。"""
        if self.phase == "pain":
            return [("导出数据，发给 gui", "Export data for gui", 0.075, WHITE),
                    ("一定会带：姿势记录、提醒记录、自测的角度、校准结果、运行记录",
                     "Always: posture log, reminders, self-test angles, calibration, run log", 0.036, GREY),
                    ("画面从来不保存，所以不会有照片或视频", "No photos or video: they are never saved", 0.036, GREY),
                    ("", "", 0.03, WHITE),
                    ("问题 1：带上自测时打的疼痛、拉扯分数吗？", "1. Include the pain and stretch scores?", 0.052, YELLOW),
                    ("这些算健康信息，你同意才带。不带也能导出，角度照样带上",
                     "They are health data. Without them, the angles still go", 0.038, GREY)], self.YES_NO
        if self.phase == "points":
            return [("问题 2：带上 7 个点的坐标吗？", "2. Include the 7 keypoint positions?", 0.055, YELLOW),
                    ("鼻子、两眼、两耳、两肩在画面里的位置，只是数字，不是照片",
                     "Nose, eyes, ears and shoulders as numbers, not a photo", 0.038, GREY),
                    ("gui 用它看你平时坐着的范围", "gui uses them to see your usual range", 0.038, GREY),
                    ("", "", 0.03, WHITE),
                    (f"刚才答的：疼痛、拉扯分数{'带上' if self.ans['pain'] else '不带'}",
                     f"1. Scores: {'include' if self.ans['pain'] else 'leave out'}", 0.036, GREY)], self.YES_NO
        if self.phase == "working":
            return [("正在打包……", "Packing...", 0.075, WHITE),
                    ("记录多的话要等一会儿（一个月的记录约半分钟）", "This can take a while", 0.04, GREY)], []
        if self.phase == "ok":
            name = os.path.basename(self.path)
            return [("打包好了", "Done", 0.08, GREEN), (name, name, 0.036, WHITE),
                    ("访达里已经选中了它：用微信、邮件或者隔空投送发给 gui", "It is selected in Finder: send it to gui",
                     0.042, WHITE),
                    (export_summary(self.man), export_summary(self.man, cn=False), 0.034, GREY),
                    ("它在“个人文件夹 / 体态测试数据 / 导出”里，发完可以删掉", "Saved in ~/体态测试数据/导出", 0.034,
                     GREY)], self.OK
        return [("没导出成功", "Export failed", 0.08, YELLOW), (self.error or "", "", 0.04, WHITE),
                ("请把“运行记录.txt”发给 gui", "Please send 运行记录.txt to gui", 0.038, GREY)], self.OK


def open_camera(index, ui=None, tries=5):
    """打开摄像头。macOS 第一次会弹窗问要不要允许，点之前打不开，所以要多试几次。
    有窗口（应用模式）时一直试，窗口里写清楚怎么办，用户点了退出或关窗口返回 None；没有窗口时试 tries 次。"""
    t0, n = time.time(), 0
    while ui is not None or n < tries:
        cap = cv2.VideoCapture(index)
        if cap.isOpened() and cap.read()[0]:
            return cap
        cap.release()
        n += 1
        if ui is None:
            time.sleep(1.5)
            continue
        canvas = ui.camera_screen(time.time() - t0)
        for _ in range(30):  # 约 1.5 秒后再试，这期间窗口照常响应
            cv2.imshow(ui.win, canvas)
            k = ui.key(50)
            if k == ord("q"):
                return None
            if k == ord("s"):
                open_path(CAMERA_SETTINGS)
    return None


def self_check(a):
    """打包后自检：模型能加载、能跑一帧、中文字体找得到。打一行 OK 开头的结果。"""
    t0 = time.time()
    det = load_detector(a.model)
    det(np.zeros((720, 1280, 3), np.uint8))
    gui = next((g for g in ("Cocoa", "GTK", "QT", "Win32") if g in cv2.getBuildInformation()), "none")
    print(f"OK model={os.path.basename(a.model)} chinese_font={'yes' if cn_font(40) else 'no'} "
          f"opencv_gui={gui} {time.time() - t0:.1f}s")
    return 0


def main(argv=None):
    global APP
    ap = argparse.ArgumentParser(description="摄像头体态检测（Mac）。打包成应用时双击打开就是应用模式。")
    ap.add_argument("--camera", type=int, default=0, help="摄像头编号，Mac 自带一般是 0")
    ap.add_argument("--interval", type=float, default=None,
                    help="几秒分析一次。默认 2.5 秒，模仿乐鑫板子；应用模式默认 1 秒")
    ap.add_argument("--alert-after", type=float, default=180,
                    help="看过去多少秒：这段时间里同一问题占到 --share 以上就提醒。默认 180 秒；测试时可设 10")
    ap.add_argument("--share", type=float, default=0.7, help="占比多少算“一直这样”，默认 0.7，允许中间有小动作")
    ap.add_argument("--sit-limit", type=float, default=45, help="连续坐多少分钟提醒起来活动，默认 45")
    ap.add_argument("--log", default=None,
                    help="写纯数字 CSV 的路径，例如 log.csv；提醒和提醒后有没有改过来记在旁边的 log-alerts.csv。"
                         "应用模式下一直写，默认数据文件夹里的 log.csv")
    ap.add_argument("--baseline", default="baseline.json", help="校准结果保存在哪")
    ap.add_argument("--selftest", action="store_true", help="一打开就做颈肩自测（平时在预览窗口里按 t）")
    ap.add_argument("--selftest-file", default="selftest.csv", help="自测结果存在哪，默认 selftest.csv")
    ap.add_argument("--calibrate", action="store_true", help="一打开就开始校准（坐正、看屏幕）")
    ap.add_argument("--once", action="store_true",
                    help="和 --selftest 或 --calibrate 一起用：做完就退出，不进平时的监测（给桌宠的菜单用）")
    ap.add_argument("--font", default=None, help="写中文大字用的字体文件。一般不用填，会自动找 Mac 自带的")
    ap.add_argument("--model", default=None,
                    help=".pt 用 Ultralytics 跑（要装 PyTorch）；.onnx 用 onnxruntime 跑（不用 PyTorch，结果一样）。"
                         "默认 yolo11n-pose.pt；应用模式默认用自带的 yolo11n-pose.onnx")
    ap.add_argument("--no-preview", action="store_true",
                    help="不显示画面，在后台跑（按键用不了，要先校准过）")
    ap.add_argument("--app", action="store_true",
                    help="应用模式：不靠终端，提示和按钮都在窗口里，数据存在 --data-dir。打包成应用时自动打开")
    ap.add_argument("--data-dir", default=APP_DATA, help="应用模式的数据文件夹，默认 ~/体态测试数据")
    ap.add_argument("--check", action="store_true", help="只检查模型和字体能不能用，打一行结果就退出（打包后自检用）")
    ap.add_argument("--export", action="store_true",
                    help="把数据文件夹（--data-dir）打成一个 zip，好发给 gui，放在里面的“导出”文件夹，打好就退出；"
                         "最后一行打 zip 的路径。不开摄像头，后台正在跑时也能用")
    ap.add_argument("--with-pain", action="store_true",
                    help="和 --export 一起用：带上自测时打的疼痛、拉扯分数（健康信息，用户单独同意了才加）")
    ap.add_argument("--with-points", action="store_true",
                    help="和 --export 一起用：带上 7 个点的原始坐标（用户单独同意了才加）")
    # 老版本的 macOS 从 Finder 打开时会多给一个 -psn_ 开头的参数，不认它
    args = [x for x in (sys.argv[1:] if argv is None else argv) if not x.startswith("-psn_")]
    a = ap.parse_args(args)
    APP = a.app or bool(getattr(sys, "frozen", False))
    if a.once and not (a.selftest or a.calibrate):
        ap.error("--once 要和 --selftest 或 --calibrate 一起用")
    if a.selftest and a.calibrate:
        ap.error("--selftest 和 --calibrate 一次只能用一个")
    if (a.selftest or a.calibrate) and a.no_preview:
        ap.error("自测和校准要在窗口里看大字，不能和 --no-preview 一起用。")
    if (a.with_pain or a.with_points) and not a.export:
        ap.error("--with-pain、--with-points 只能和 --export 一起用")
    if a.font:
        FONT_FILES.insert(0, a.font)
    if a.model is None:
        a.model = bundled("yolo11n-pose.onnx") if APP else "yolo11n-pose.pt"
    if a.interval is None:
        a.interval = 1.0 if APP else 2.5
    if a.check:
        return self_check(a)
    if a.export:
        return export_cli(a)

    saved = sys.stdout, sys.stderr

    # 桌宠暂停时会让它退出（SIGTERM）：跟 Ctrl+C 一样处理。在判断的时候收到，先把数据存好（见 run）
    def on_term(signum, frame):
        raise KeyboardInterrupt
    old_term = signal.signal(signal.SIGTERM, on_term)
    try:
        return run(a, args)
    except KeyboardInterrupt:  # 还没开始判断就被停掉了，比如还在等摄像头
        print("收到退出信号，退出。")
        return 0
    except Exception:
        if not APP:
            raise
        traceback.print_exc()  # 写进运行记录
        alert(f"{APP_NAME}出错了，要关掉。请把“体态测试数据”文件夹里的“{RUNLOG}”发给 gui。")
        return EXIT_ERROR
    finally:
        signal.signal(signal.SIGTERM, old_term)
        release_lock()
        if isinstance(sys.stdout, RunLog):
            sys.stdout.close()
        sys.stdout, sys.stderr = saved


def run(a, args):
    global SOURCE
    data = report = ui = None
    MSGS.clear()
    if APP:
        data = os.path.abspath(os.path.expanduser(a.data_dir))
        os.makedirs(data, exist_ok=True)
        sys.stdout = sys.stderr = RunLog(os.path.join(data, RUNLOG), terminal(sys.__stdout__))
        sys.stdout.capture_native()
        print(f"===== 打开{APP_NAME}，参数：{' '.join(args) or '没有'} =====")
        here = lambda p: p if os.path.isabs(p) else os.path.join(data, p)
        a.baseline, a.selftest_file, a.log = here(a.baseline), here(a.selftest_file), here(a.log or "log.csv")
        report = os.path.join(data, REPORT)
        if take_lock(data) is None:
            fatal(f"{APP_NAME}已经在运行了（可能是桌宠在后台开着的）。先在桌宠里暂停，或者关掉另一个{APP_NAME}窗口，"
                  "再打开。", EXIT_RUNNING, dialog=not a.no_preview)
    SOURCE = f"mac:{os.path.basename(a.model)}"
    version = algo_version()
    print(f"算法版本 {version}，点来自 {SOURCE}")
    try:
        detect = load_detector(a.model)
    except Exception as e:
        fatal(f"模型打不开：{a.model}（{e}）", dialog=not a.no_preview)

    baseline = None
    if os.path.exists(a.baseline):
        with open(a.baseline) as f:
            baseline = json.load(f)
        if {k for k in baseline if not k.startswith("_")} != set(METRIC_KEYS):  # 旧版本存的校准，项目对不上
            baseline = None
            say("之前的校准是旧版本做的，请坐正后按 c 重新校准。")
        else:
            say("读取了之前的校准（换了位置就按 c 重新校准）" if APP else
                f"读取了之前的校准：{a.baseline}（换了位置就按 c 重新校准）")
    elif not a.calibrate and not (APP and a.no_preview):
        print("还没校准：坐正、看屏幕，然后在预览窗口里按 c。")
    if baseline is None and a.no_preview and APP:
        fatal("还没校准：先打开窗口校准一次（双击应用按 C，或者用 --calibrate --once），再在后台跑。",
              EXIT_NO_BASELINE, dialog=False)

    if APP and not a.no_preview:
        ui = AppUI(WIN_APP)
    cap = open_camera(a.camera, ui)
    if cap is None:
        if ui is not None:  # 用户在等摄像头的窗口里点了退出
            print("没打开摄像头，用户退出了。")
            cv2.destroyAllWindows()
            return EXIT_NO_CAMERA
        fatal("打不开摄像头。确认 系统设置 → 隐私与安全性 → 摄像头 里" +
              ("这个应用（被桌宠启动时是桌宠）已打开" if APP else "“终端”已打开") +
              "；再试一次，或者加 --camera 1 换个摄像头。", EXIT_NO_CAMERA, dialog=False)

    log_f = writer = alert_f = alert_w = None
    if a.log:
        log_f, writer = open_csv(a.log, LOG_COLS)
        alert_f, alert_w = open_csv(alerts_path(a.log), ALERT_COLS)
    follow = AlertFollow(alert_w, alert_f)  # 每次提醒之后有没有改过来

    history, calib, calibrating = [], [], a.calibrate
    if calibrating:
        say("开始校准：坐正，看屏幕中间，别动……")
    label = "normal"
    alerts = ShareAlerts(a.alert_after, a.share)
    sit = SitTimer(a.sit_limit * 60)
    totals = {k: 0 for k in NAMES}
    total_evals = 0
    last_run = 0.0
    quit_at = None  # --calibrate --once：校准完停两秒让人看到“校准完成”再退出
    xy = conf = None
    status = ["Press c to calibrate"] if baseline is None else ["Monitoring"]
    app_status, app_warn, app_note, details = "还没开始判断", False, "", []
    new_selftest = lambda now: SelfTest(a.selftest_file, now, report=report)
    st = new_selftest(time.time()) if a.selftest else None  # 正在做的自测
    ex = None  # 正在导出数据（只在窗口里）
    if st:
        st.once = a.once

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                if not APP:
                    print("读不到画面")
                    break
                say("读不到画面，重新打开摄像头……")
                cap.release()
                cap = open_camera(a.camera, ui, tries=10)
                if cap is None:
                    break
                continue
            now = time.time()
            if quit_at and now >= quit_at:
                break

            if now - last_run >= (0.2 if st else 0.5 if calibrating else a.interval):
                last_run = now
                t0 = time.time()
                out = detect(frame)
                xy, conf = out[0], out[1]
                persons = out[2] if len(out) > 2 else (1 if xy is not None else 0)
                infer_ms = (time.time() - t0) * 1000
                crowded = persons > 1  # 画面里不止一个人：不知道哪个是你，判断、校准、自测都暂停
                m = compute_metrics(xy, conf, frame.shape[0]) if xy is not None and not crowded else None
                issues = None

                sat = sit.update(now, xy is not None)
                if sat:
                    text = f"已经连续坐了 {int(sat / 60)} 分钟，起来走两分钟吧"
                    notify(text)
                    say(f"提醒：{text}" if APP else f"提醒：已经连续坐了 {int(sat / 60)} 分钟")
                    if ui:
                        ui.show_banner(text, now)
                    follow.add(now, "sitting", sit_min=sat / 60)

                if st:  # 自测时平时的判断、提醒和记录都暂停
                    st.feed(now, m)
                elif crowded:
                    history = []  # 回到一个人时从头平滑，不把别人的数混进来
                    status = [f"{persons} people in view: paused"]
                    app_status, app_warn, app_note = f"看到 {persons} 个人：只有你一个人时才判断", True, ""
                    if writer and baseline is not None:  # 只记时间和人数，说明这段为什么没有判断
                        writer.writerow([f"{now:.2f}", label, "", ""]
                                        + [""] * (len(LOG_METRICS) + len(RAW_KEYS) + len(KP_COLS))
                                        + [persons, f"{infer_ms:.0f}", version, SOURCE])
                        log_f.flush()
                elif m is None:
                    status = ["Can't see eyes or ears"]
                    app_status, app_warn, app_note = "看不到你的脸：坐到摄像头正前方", True, ""
                elif calibrating:
                    calib.append(m)
                    status = [f"Calibrating... {len(calib)}/5  sit upright"]
                    if len(calib) >= 5:
                        # _raw（校准时的眼睛高度、耳距）内存里也要有：10-27 以前只写进了文件，重开应用之前
                        # 弯腰/往后靠/滑坐都判不了（gui 10-27 23:03 刚按过 C，弯腰就没显示出来）
                        baseline = dict(median_of(calib), _raw=median_of(calib, RAW_KEYS))
                        with open(a.baseline, "w") as f:  # 带上版本和时间，以 _ 开头的不是校准的量
                            json.dump(dict(baseline, _algo_version=version, _time=round(now)), f, indent=1)
                        calibrating, calib, history = False, [], []
                        alerts.reset()
                        say("校准完成" if APP else f"校准完成，已保存到 {a.baseline}")
                        if baseline["shoulder_width"] is None:
                            say("注意：校准时没看到两边肩膀，头前伸、耸肩、高低肩这次都不会判断。"
                                "往后坐一点或把屏幕往后仰，再按 c。")
                        status = ["Calibrated. Monitoring"]
                        app_status, app_warn, app_note = "校准完成，开始判断", False, ""
                        if a.once:
                            quit_at = now + 2
                elif baseline is not None:
                    history = (history + [m])[-5:]
                    issues, sev, d = assess(median_of(history, METRIC_KEYS + RAW_KEYS), baseline)
                    # 轻的弯腰（眼睛低了 0.2~0.4 个耳距）只记录、不提醒
                    shares, fired = alerts.update(now, [k for k in issues if k != "slouch" or d["slouch_alert"]])
                    for k, sh in fired:
                        name = issue_name(k, d) if k in issues else NAMES[k]
                        text = f"过去 {int(a.alert_after)} 秒里，有 {sh:.0%} 的时间{name}"
                        notify(text)
                        say(f"提醒：{text}" if APP else f"提醒：过去 {int(a.alert_after)} 秒里 {sh:.0%} 的时间{name}")
                        if ui:
                            ui.show_banner(text, now)
                        follow.add(now, "posture", k, issue_side(k, d) if k in issues else "", sh, a.alert_after)
                    total_evals += 1
                    for k in issues:
                        totals[k] += 1
                    status = status_lines(issues, sev, d, shares, a, sit.minutes(now), label, infer_ms)
                    if m["shoulder_width"] is None:
                        status.insert(1, "shoulders not in view: fwd / shrug / shoulders off")
                    app_status, app_warn = "现在：" + issue_text_cn(issues, d), bool(issues)
                    app_note = "看不到两边肩膀：头前伸、耸肩、高低肩先不判断" if m["shoulder_width"] is None else ""
                    if not APP:
                        print(time.strftime("%H:%M:%S"), issue_text_cn(issues, d), f"程度 {sev:.2f}",
                              f"[标记:{label}]")
                    if writer:
                        fmt = lambda v: "" if v is None else f"{v:.4f}"
                        raw = median_of(history, RAW_KEYS)  # 和判断用的量一样，取最近 5 次的中位数
                        writer.writerow([f"{now:.2f}", label, "|".join(issues), f"{sev:.2f}"]
                                        + [fmt(d[k]) for k in LOG_METRICS] + [fmt(raw[k]) for k in RAW_KEYS]
                                        + [f"{xy[i][a]:.1f}" if conf[i] > MIN_SCORE else "" for i in range(7) for a in (0, 1)]
                                        + [persons, f"{infer_ms:.0f}", version, SOURCE])
                        log_f.flush()
                if not st and not calibrating:
                    follow.update(now, xy is not None, issues)
            if st:
                st.update(now)

            if a.no_preview:
                time.sleep(0.05)
            else:
                if ui:
                    view, s = frame, APP_W / frame.shape[1]
                    view = cv2.resize(frame, (APP_W, int(round(frame.shape[0] * s))), interpolation=cv2.INTER_AREA) \
                        if s != 1 else frame.copy()
                else:
                    view, s = frame.copy(), 1.0
                mode = "selftest" if st else "calibrating" if calibrating else \
                    "need_calib" if baseline is None else "monitor"
                if st or ex or (ui and mode != "monitor"):
                    view = view // 3  # 画面调暗，大字看得清
                if xy is not None:
                    for i in range(7):
                        if conf[i] > MIN_SCORE:
                            cv2.circle(view, (int(xy[i][0] * s), int(xy[i][1] * s)), 6 if s >= 1 else 5,
                                       (0, 255, 0), -1)
                if st:
                    draw_big(view[:ui.bar_top(view)] if ui else view, st.screen(now))
                    if ui:
                        ui.draw_selftest_bar(view, st)
                elif ex:
                    ui.draw_export(view, ex)
                elif ui:
                    details = status if mode == "monitor" else []
                    ui.draw_monitor(view, now, mode, app_status, app_warn, app_note, sit.minutes(now), details,
                                    len(calib))
                else:
                    draw_text(view, status, (0, 255, 255) if "ISSUE" in status[0] else (255, 255, 255))
                cv2.imshow(WIN_APP if ui else "posture (nothing is saved)", view)
                key = ui.key(30) if ui else cv2.waitKey(30) & 0xFF
                if key == ord("q"):
                    if st:
                        st.on_quit(now)
                    break
                if ui and key == ord("o"):  # 打开数据文件夹
                    open_path(data)
                elif st:
                    st.key(key, now)
                elif ex:
                    ex.key(key, now)
                elif ui and key == ord("d"):
                    ui.details = not ui.details
                elif ui and key == ord("e"):
                    if calibrating:
                        say("校准完再导出。")
                    else:
                        ex = ExportAsk(data)
                elif key == ord("t"):
                    calibrating, calib = False, []
                    follow.close_all(now, "interrupted")
                    st = new_selftest(now)
                elif key == ord("c"):
                    calibrating, calib = True, []
                    follow.close_all(now, "interrupted")
                    say("开始校准：坐正，看屏幕中间，别动……")
                elif key in LABELS:
                    label = LABELS[key]
                    say(f"标记改为：{label}")

            if ex:
                ex.update(now)
                if ex.done:
                    ex = None
            if st and st.done:  # 自测结束，回到平时的监测
                if a.once:
                    break
                st, history = None, []
                alerts.reset()
                status = ["Monitoring"] if baseline is not None else ["Press c to calibrate"]
                say("回到平时的监测。")
    except KeyboardInterrupt:  # Ctrl+C，或者桌宠让它退出
        print("收到退出信号，存好数据再退出。")
        if st:
            st.on_quit(time.time())
    finally:
        follow.close_all(time.time(), "ended")
        if cap is not None:
            cap.release()
        if not a.no_preview:
            cv2.destroyAllWindows()
        if ex:
            ex.wait()
        for f in (log_f, alert_f):
            if f:
                f.close()
    if total_evals:
        print(f"\n这次一共判断了 {total_evals} 次（约 {total_evals * a.interval / 60:.0f} 分钟），各姿势占的时间：")
        for k in NAMES:
            print(f"  {NAMES[k]}：{totals[k] / total_evals:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
