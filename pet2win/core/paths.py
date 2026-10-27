"""桌宠自己的文件夹和体态测试的数据文件夹在哪。只用标准库。

- Windows：桌宠的在 %LOCALAPPDATA%\\BeibeijiaPet2（对应 Mac 的 ~/Library/Application Support/BeibeijiaPet2），
  体态测试的在 ~\\体态测试数据（和后端默认的一样）。放 Local 不放 Roaming：公司电脑的漫游配置会把文件同步到服务器上。
- 在 Mac 上开发、试界面时：默认放到 ~/.beibeijia-pet2-dev/ 下面，绝不碰 Mac 版真正在用的文件夹和数据。
- 环境变量 PET2_DATA_DIR / PET2_BACKEND_DIR 可以改（测试用）。
"""
import os
import sys

DEV_ROOT = os.path.join(os.path.expanduser("~"), ".beibeijia-pet2-dev")


def data_directory():
    env = os.environ.get("PET2_DATA_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "BeibeijiaPet2")
    return os.path.join(DEV_ROOT, "BeibeijiaPet2")


def backend_directory():
    env = os.environ.get("PET2_BACKEND_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if sys.platform == "win32":
        return os.path.join(os.path.expanduser("~"), "体态测试数据")
    return os.path.join(DEV_ROOT, "体态测试数据")
