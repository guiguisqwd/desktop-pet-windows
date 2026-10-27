# -*- mode: python ; coding: utf-8 -*-
# 在 Windows 上把桌宠打包成 BeibeijiaPet.exe（文件夹形式，build_windows.ps1 之后改名成 赛博背背佳.exe）。
# 素材（assets/）和同意书全文（resources/）一起带进去。不直接跑这个文件：运行 packaging/build_windows.ps1。
import os

HERE = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(HERE)

a = Analysis(
    [os.path.join(HERE, "pet_entry.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "assets"), "assets"), (os.path.join(ROOT, "resources"), "resources")],
    # 桌宠只用 QtCore / QtGui / QtWidgets；其余的 Qt 模块和用不到的大包不带
    excludes=["tkinter", "numpy", "cv2", "onnxruntime", "PIL", "matplotlib", "pandas", "scipy", "IPython",
              "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore", "PySide6.QtMultimedia",
              "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtOpenGL", "PySide6.QtPdf", "PySide6.QtSvg", "PySide6.QtDBus"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [("X utf8", None, "OPTION")], exclude_binaries=True, name="BeibeijiaPet", console=False,
          icon=os.path.join(HERE, "icon.ico"), upx=False, version=os.path.join(HERE, "version_pet.txt"))
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="BeibeijiaPet")
