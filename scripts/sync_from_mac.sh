#!/bin/bash
# 跟一次 Mac 版的大版本（在 Mac 上运行）：
#   1. 后端 posture_cam.py 和模型原样复制过来（默认从 ../.posture-build/mac-yolo，可以用 BACKEND_SRC=... 指定）
#   2. 素材、同意书（换成 Windows 的说法）复制过来
#   3. 跑 Windows 版的核心测试；编 Mac 版的 Swift Core 和 Python 版对拍，重新存对拍结果
# Mac 的 Core（Pet2/Core/*.swift）改了的话，要先照着改 pet2win/core/ 里对应的 .py，不然第 3 步的对拍会报不一样的地方。
# 只读 Mac 版的文件，不改它们。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
BACKEND_SRC="${BACKEND_SRC:-$ROOT/.posture-build/mac-yolo}"
PY="${PY:-$HERE/.venv/bin/python}"

echo "== 1/3 后端：$BACKEND_SRC"
cp "$BACKEND_SRC/posture_cam.py" "$HERE/backend/posture_cam.py"
cp "$BACKEND_SRC/yolo11n-pose.onnx" "$HERE/backend/yolo11n-pose.onnx"
grep -m1 '^ALGO_VERSION' "$HERE/backend/posture_cam.py"

echo "== 2/3 素材和同意书"
cp "$ROOT/Assets/idle.png" "$ROOT/Assets/blink.png" "$ROOT/Assets/rig-clean-v5.png" "$ROOT/Assets/whale-heart.gif" "$HERE/assets/"
rsync -a --delete --exclude ".DS_Store" "$ROOT/Assets/whale/" "$HERE/assets/whale/"
"$PY" "$HERE/scripts/make_consent_windows.py"

echo "== 3/3 测试和对拍"
cd "$HERE"
"$PY" -m tests
"$PY" difftest/difftest.py run
echo
echo "好了。看一眼 git diff，没问题就提交、推到 GitHub，Actions 会在 Windows 上测试并打包。"
