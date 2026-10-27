# -*- mode: python ; coding: utf-8 -*-
# 在 Windows 上把体态测试打包成 PostureTest.exe（文件夹形式）。照 Mac 的 posture_app.spec：不带 PyTorch 和 Ultralytics，
# 用自带的 yolo11n-pose.onnx 和 onnxruntime 跑，找到的点和 Mac 一样。入口是 posture_win.py（Windows 补丁 + 原样的 posture_cam.py）。
# 不直接跑这个文件：在 Windows 上运行 packaging/build_windows.ps1（GitHub Actions 也是调它）。
import os

HERE = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    [os.path.join(HERE, "posture_win.py")],
    pathex=[HERE],
    datas=[(os.path.join(HERE, "yolo11n-pose.onnx"), ".")],
    # 用不到的大包，开发环境里装了也不带进去
    excludes=["ultralytics", "torch", "torchvision", "onnx", "tkinter", "matplotlib", "pandas", "scipy",
              "IPython", "PyQt5", "PyQt6", "PySide2", "PySide6", "polars", "sympy"],
)
pyz = PYZ(a.pure)
# X utf8：不管系统的语言和编码设置是什么，读写文件、打印都用 UTF-8（中文 Windows 默认是 GBK，不加会出错）
exe = EXE(pyz, a.scripts, [("X utf8", None, "OPTION")], exclude_binaries=True, name="PostureTest", console=False,
          icon=os.path.join(HERE, "..", "packaging", "icon.ico"), upx=False,
          version=os.path.join(HERE, "..", "packaging", "version_backend.txt"))
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="PostureTest")
