"""跑 core 的全部测试：python -m tests（在 Pet2-Windows 文件夹里）。和 Mac 的 bash scripts/test.sh 一样，不要摄像头。"""
import sys
import traceback

from . import support, test_main, test_onboarding, test_rewards


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows 的控制台默认不是 UTF-8
    except (AttributeError, ValueError):
        pass
    # 顺序和 Swift 版一样：main.swift 的各段，然后 runOnboardingTests()、runRewardTests()
    for test in test_main.ALL + test_onboarding.ALL + test_rewards.ALL:
        try:
            test()
        except support.Fatal as e:
            print(f"✗ {test.__module__}.{test.__name__} 停下了：{e}")
            break
        except Exception:
            support.failures += 1
            print(f"✗ {test.__module__}.{test.__name__} 出错了：")
            traceback.print_exc()
    if support.failures == 0:
        print(f"✓ Pet2 Windows 核心：{support.checks} 项检查全部通过")
        return 0
    print(f"✗ Pet2 Windows 核心：{support.failures}/{support.checks} 项没通过")
    return 1


if __name__ == "__main__":
    sys.exit(main())
