# 赛博背背佳 Windows 版（Pet2-Windows）

2026-10-27 开始做。Mac 版（`../Pet2`）是主版本，天天改；Windows 版隔一段时间跟一次大版本。Mac 版的文件这里只读，一个都不改。

用 Python + PySide6 写，GitHub Actions 的 Windows 机器打包。体态测试后端（`posture_cam.py`）和 Mac 版是同一份代码、同一个模型。

## 怎么做到“和 Mac 版一模一样”

| 部分 | 做法 | 怎么验证 |
| --- | --- | --- |
| 决定（Brain、提醒规则、奖励、同意规则、导出……） | `pet2win/core/` 逐个文件照翻 `Pet2/Core/*.swift`，只用 Python 标准库 | 1. `Pet2/Tests` 的全部检查照翻成 `tests/`，全过<br>2. **对拍**：同一批输入（220 段随机剧情、几千个纯函数输入、一串串文件操作）Swift 跑一遍、Python 跑一遍，每个事件、每一帧画面、每个占比**按二进制位**比，一位都不差 |
| 弹簧动作 | `pet2win/app/animator.py` 照翻 `Pet2/Mac/Animator.swift` | 对拍 4 万帧姿势，一位都不差 |
| 画鲸鱼、气泡、奖励 GIF | QPainter 照翻 Core Graphics 的画法 | 和 Mac 版离线渲染的截图逐张比（`tools/preview.py`），角色部分平均每像素差不到 2/255 |
| 后端 | `backend/posture_cam.py` 原样复制，`backend/posture_win.py` 在外面补 Windows 的东西 | 打包后在 Windows 上自检；测锁、测“停”、测导出（`tests/windows_backend_test.py`） |

Swift 和 Python 在小地方不一样（比如 Swift 的 `Double(" 12")` 是 nil、Python 的 `float(" 12")` 是 12），这些都集中写在 `pet2win/core/swiftcompat.py`，在 Mac 上逐个核对过。

## 常用命令（在这个文件夹里）

```bash
# 第一次：建开发环境（Mac 上）
~/.local/bin/uv venv --python 3.12 .venv && ~/.local/bin/uv pip install --python .venv/bin/python PySide6-Essentials pillow

.venv/bin/python -m tests                      # 核心测试（照翻 Mac 的全部检查）
.venv/bin/python difftest/difftest.py run      # 和 Mac 版对拍（编 ../Pet2/Core，只读），并更新 difftest/golden/
.venv/bin/python difftest/difftest.py check    # 只跑 Python，和存着的 Swift 结果比（Windows 上用这个）
.venv/bin/python tests/smoke_app.py            # 整个桌宠不开窗口跑一遍：提醒 → 改过来 → 奖励（约 45 秒）
.venv/bin/python tools/preview.py ../Assets 输出文件夹 --postures   # 离线渲染每种姿势，和 Mac 的 preview 对比
.venv/bin/python -m pet2win                    # 在 Mac 上开桌宠看看（数据放在 ~/.beibeijia-pet2-dev，不碰 Mac 版的数据）
```

打包只能在 Windows 上：`python packaging\build_windows.py`。平时不用自己打：推到 GitHub，Actions 会跑全部测试、打包、自检，
zip 在那次运行的 Artifacts 里；推一个 `v0.1.1` 这样的标签，会再建一个草稿 Release。

## 跟一次 Mac 版

1. Mac 版的 `Pet2/Core/*.swift` 改了哪些，就照着改 `pet2win/core/` 里同名的 `.py`（一行对一行，函数名换成小写下划线）。
   Mac 版的画面层（`Pet2/Mac/`）改了，照着改 `pet2win/app/` 里对应的文件。
2. `bash scripts/sync_from_mac.sh`：复制后端、模型、素材、同意书，跑测试，重新对拍。对拍有不一样的地方会列出来。
3. 提交、推到 GitHub，等 Actions 变绿，下载 zip。

## 代码在哪

```
pet2win/core/       照翻 Pet2/Core（只用标准库）——决定全在这里
  swiftcompat.py    Swift 和 Python 不一样的细节
  brain.py …        和 Swift 文件一一对应（见 core/__init__.py）
  paths.py          桌宠的文件夹：Windows 是 %LOCALAPPDATA%\BeibeijiaPet2；体态测试的：~\体态测试数据
  fileio.py         读别人正在写的文件时，不挡着体态测试改名（FILE_SHARE_DELETE）
pet2win/app/        Windows 的画面（PySide6），对应 Pet2/Mac
  main.py           AppDelegate + main：托盘、右键菜单、读文件、计时、点穿
  pet_canvas.py     PetCanvas：画角色、气泡、爱心、脚下一行字
  animator.py       Animator：弹簧动作
  whale_rig.py      WhaleRig：一张原画切块摆姿势
  reward_show.py    RewardShow + GIFClip：奖励 GIF
  posture_app.py    PostureApp：管后台的体态测试
  onboarding_window.py / my_data.py / form_kit.py   首次引导、我的数据、表单零件
  platform_win.py   Windows 的系统接口：点穿、作业（桌宠退出后台跟着退）、叫后台停、相机权限、打开文件夹
backend/            posture_cam.py（Mac 版原样）+ posture_win.py（Windows 补丁）+ 模型 + PyInstaller 配置
assets/ resources/  素材、同意书（Windows 说法，scripts/make_consent_windows.py 从 Mac 版生成）
tests/ difftest/    测试和对拍
packaging/          打包脚本、图标、安装说明
```

## 和 Mac 版不一样的地方（都是因为系统不同）

- 菜单栏图标 → 右下角托盘图标；右键鲸鱼娘的菜单一样。
- 后台停：Mac 发 SIGTERM；Windows 点亮一个有名字的事件，后端入口收到后当 Ctrl+C 处理（和 Mac 一样存好数据再退）。
- 后台放进 Windows 的“作业”：桌宠退出或崩溃，体态测试跟着关，不会留下开着摄像头的进程。
- 摄像头权限：Mac 问 AVFoundation；Windows 读设置里的相机开关，关了就提示去哪里打开。
- 文字里的“Mac / 访达 / 隔空投送 / 系统设置”换成“电脑 / 资源管理器 / U 盘 / Windows 设置”。同意书只换了这几个词，条款没动（gui 要再看一眼）。
- 为了省电：平常坐着 30 帧/秒，提醒、奖励、换动作时 60 帧/秒；身子那块网格按稍粗的精度缓存（屏幕上看不出来）。
