"""把 Mac 版的同意书（Pet2/Resources/同意书-第2版.txt）换成 Windows 的说法，写进 resources/。
只换和电脑系统有关的词（Mac → 电脑、数据放在哪、隔空投送），条款一个字不改。Mac 版改了同意书就重新跑一次；
哪一句对不上会直接报错，免得漏换。用法：python scripts/make_consent_windows.py"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(os.path.dirname(ROOT), "Pet2", "Resources", "同意书-第2版.txt")
DST = os.path.join(ROOT, "resources", "同意书-第2版.txt")

PAIRS = [
    ("（第 2 版，Mac 版，", "（第 2 版，Windows 版，"),
    ("这个产品做什么。 你的 Mac 用摄像头", "这个产品做什么。 你的电脑用摄像头"),
    ("摄像头画面只在你的 Mac 内存里实时处理", "摄像头画面只在你的电脑内存里实时处理"),
    ("产品会在你的 Mac 上记录：", "产品会在你的电脑上记录："),
    ("这些数据只存在你自己的 Mac 上：~/体态测试数据 和 ~/Library/Application Support/BeibeijiaPet2。",
     "这些数据只存在你自己的电脑上：个人文件夹里的“体态测试数据”（C:\\Users\\你的用户名\\体态测试数据）和 %LOCALAPPDATA%\\BeibeijiaPet2。"),
    ("请当面用隔空投送，或者用我们给你的上传链接发", "请当面拷给我们（U 盘、数据线都行），或者用我们给你的上传链接发"),
    ("不勾这一项，它们只存在你的 Mac 上，", "不勾这一项，它们只存在你的电脑上，"),
]


def main():
    with open(SRC, encoding="utf-8") as f:
        text = f.read()
    for a, b in PAIRS:
        if text.count(a) != 1:
            sys.exit(f"Mac 版同意书里找不到这一句（或者不止一处），请手动看一下：{a}")
        text = text.replace(a, b)
    leftover = [line for line in text.splitlines() if "Mac" in line or "隔空" in line or "访达" in line]
    if leftover:
        sys.exit(f"还有和 Mac 有关的说法没换：{leftover}")
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    with open(DST, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"已写 {DST}")


if __name__ == "__main__":
    main()
