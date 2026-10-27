"""素材、同意书全文、后台的体态测试在哪。

打包好的样子（PyInstaller 的 onedir）：
    赛博背背佳-Windows/
      赛博背背佳.exe
      _internal/            Python、PySide6、素材（assets/）、同意书（resources/）
      体态测试/PostureTest.exe（后台，另外打的包）
在 Mac 上开发时：素材读 ../Assets，同意书读 ../Pet2/Resources。环境变量 PET2_ASSETS / PET2_BACKEND_EXE 可以改。
"""
import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))          # Pet2-Windows/
REPO_ROOT = os.path.dirname(PROJECT)                       # 桌宠背背佳/（开发时）


def bundle_dir():
    """PyInstaller 解出来的文件在哪（onedir 是 exe 旁边的 _internal）。"""
    return getattr(sys, "_MEIPASS", PROJECT)


def app_dir():
    """exe 所在的文件夹；开发时是 Pet2-Windows/。"""
    return os.path.dirname(sys.executable) if FROZEN else PROJECT


def assets_dir():
    env = os.environ.get("PET2_ASSETS")
    if env:
        return env
    for candidate in (os.path.join(bundle_dir(), "assets"), os.path.join(PROJECT, "assets"), os.path.join(REPO_ROOT, "Assets")):
        if os.path.exists(os.path.join(candidate, "idle.png")):
            return candidate
    return os.path.join(bundle_dir(), "assets")


def consent_text_path():
    for candidate in (os.path.join(bundle_dir(), "resources", "同意书-第2版.txt"),
                      os.path.join(PROJECT, "resources", "同意书-第2版.txt"),
                      os.path.join(REPO_ROOT, "Pet2", "Resources", "同意书-第2版.txt")):
        if os.path.exists(candidate):
            return candidate
    return None


BACKEND_NAMES = ("PostureTest.exe", "PostureTest")


def backend_executable():
    """后台的体态测试：环境变量 → 桌宠旁边的“体态测试”文件夹。找不到返回 None。"""
    env = os.environ.get("PET2_BACKEND_EXE")
    if env:
        return env if os.path.exists(env) else None
    for folder in ("体态测试", "PostureTest"):
        for name in BACKEND_NAMES:
            path = os.path.join(app_dir(), folder, name)
            if os.path.isfile(path) and (sys.platform == "win32" or os.access(path, os.X_OK)):
                return path
    return None
