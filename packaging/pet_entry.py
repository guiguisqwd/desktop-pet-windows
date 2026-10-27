"""打包用的入口（PyInstaller 要一个脚本文件）。"""
import sys

from pet2win.app.main import main

if __name__ == "__main__":
    sys.exit(main())
