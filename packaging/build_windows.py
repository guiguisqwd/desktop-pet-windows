"""在 Windows 上打包赛博背背佳：先跑全部测试，再打两个包（桌宠、体态测试），拼成一个文件夹，自检，最后压成 zip。

用法（Windows，在 Pet2-Windows 文件夹里）：  python packaging\\build_windows.py
GitHub Actions 也是调它。产物：dist\\CyberBeibeijia-windows-v<版本>.zip，里面一个文件夹：
    赛博背背佳-Windows\\
      赛博背背佳.exe          双击打开桌宠
      _internal\\              桌宠的 Python、Qt、素材
      体态测试\\PostureTest.exe 后台看坐姿的程序（桌宠自己会开它）
      先看我-安装说明.txt
用 Python 写，不用 PowerShell：路径里有中文，Windows PowerShell 5 读 .ps1 默认不是 UTF-8，会乱码。
"""
import os
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, ".build")
DIST = os.path.join(ROOT, "dist")
sys.path.insert(0, ROOT)
from pet2win import __version__ as VERSION   # noqa: E402

FOLDER = "赛博背背佳-Windows"
PET_EXE = "赛博背背佳.exe"
ZIP_NAME = f"CyberBeibeijia-windows-v{VERSION}.zip"   # GitHub 的附件名只认英文


def step(text):
    print(f"\n== {text}", flush=True)


def run(cmd, **kw):
    print("   $ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=kw.pop("cwd", ROOT), **kw)


def venv(name, requirements):
    path = os.path.join(BUILD, name)
    py = os.path.join(path, "Scripts", "python.exe")
    if not os.path.exists(py):
        run([sys.executable, "-m", "venv", path])
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "-r", requirements])
    run([py, "-m", "pip", "freeze"])
    return py


def check_output(cmd, expect, timeout=180, env=None):
    """跑一个打包好的 exe（窗口程序也能把输出交给管道），最后一行要以 expect 开头。"""
    print("   $ " + " ".join(cmd), flush=True)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, env=env)
    out = res.stdout.decode("utf-8", errors="replace").strip()
    print("     " + out.replace("\n", "\n     "), flush=True)
    last = out.splitlines()[-1] if out else ""
    if res.returncode != 0 or not last.startswith(expect):
        raise SystemExit(f"自检没过（退出码 {res.returncode}）：{last}")
    return out


def main():
    if sys.platform != "win32":
        raise SystemExit("要在 Windows 上运行（或者推到 GitHub，让 Actions 的 Windows 机器打包）。")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    t0 = time.time()
    os.makedirs(BUILD, exist_ok=True)

    step("1/7 准备两个打包用的 Python 环境（第一次要下载约 300 MB）")
    py_pet = venv("venv-pet", os.path.join(ROOT, "packaging", "requirements-pet.txt"))
    py_backend = venv("venv-backend", os.path.join(ROOT, "packaging", "requirements-backend.txt"))

    step("2/7 测试：核心（照翻 Mac 版的全部检查）、对拍（和 Mac 版存下来的结果逐项比）、管后台、整个桌宠")
    run([py_pet, "-m", "tests"])
    run([py_pet, os.path.join("difftest", "difftest.py"), "check"])
    run([py_backend, os.path.join("backend", "posture_win.py"), "--windows-check"], cwd=ROOT)
    run([py_pet, os.path.join("tests", "posture_app_test.py")])   # 管后台那一层：开、停、校准、导出
    run([py_pet, os.path.join("tests", "ui_walk_test.py")])       # 引导 9 步、我的数据、改勾选、导出、删除点一遍
    run([py_pet, os.path.join("tests", "smoke_app.py")])          # 整个桌宠跑一遍：提醒 → 改过来 → 奖励

    step("3/7 打包体态测试（后台）")
    run([py_backend, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
         "--distpath", os.path.join(BUILD, "dist"), "--workpath", os.path.join(BUILD, "work-backend"),
         os.path.join("backend", "posture_win.spec")])

    step("4/7 打包桌宠")
    run([py_pet, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
         "--distpath", os.path.join(BUILD, "dist"), "--workpath", os.path.join(BUILD, "work-pet"),
         os.path.join("packaging", "pet.spec")])

    step("5/7 拼成一个文件夹")
    out = os.path.join(DIST, FOLDER)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(DIST, exist_ok=True)
    shutil.copytree(os.path.join(BUILD, "dist", "BeibeijiaPet"), out)
    os.replace(os.path.join(out, "BeibeijiaPet.exe"), os.path.join(out, PET_EXE))
    shutil.copytree(os.path.join(BUILD, "dist", "PostureTest"), os.path.join(out, "体态测试"))
    # 安装说明：带 BOM、换行用 \r\n，老版本的记事本打开也不乱码
    with open(os.path.join(ROOT, "packaging", "先看我-安装说明.txt"), encoding="utf-8") as f:
        guide = f.read().replace("\r\n", "\n").replace("\n", "\r\n")
    with open(os.path.join(out, "先看我-安装说明.txt"), "w", encoding="utf-8-sig", newline="") as f:
        f.write(guide)
    # 不带个人数据：包里不该有 csv / jsonl / baseline
    for dirpath, _, names in os.walk(out):
        for n in names:
            low = n.lower()
            if low.endswith((".csv", ".jsonl")) or low.startswith("baseline"):
                raise SystemExit(f"包里发现数据文件，停止打包：{os.path.join(dirpath, n)}")

    step("6/7 自检打包好的程序")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    check_output([os.path.join(out, "体态测试", "PostureTest.exe"), "--check"], "OK model=yolo11n-pose.onnx chinese_font=yes")
    check_output([os.path.join(out, PET_EXE), "--check"], "OK", env=env)
    run([py_pet, os.path.join("tests", "windows_backend_test.py"), os.path.join(out, "体态测试", "PostureTest.exe")])

    step("7/7 压成 zip")
    zip_path = os.path.join(DIST, ZIP_NAME)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:   # 中文文件名带 UTF-8 标记
        for dirpath, _, names in os.walk(out):
            for n in sorted(names):
                full = os.path.join(dirpath, n)
                zf.write(full, os.path.relpath(full, DIST))
    size = os.path.getsize(zip_path) / 1024 / 1024
    print(f"\n好了：{zip_path}（{size:.0f} MB），用了 {time.time() - t0:.0f} 秒", flush=True)


if __name__ == "__main__":
    main()
