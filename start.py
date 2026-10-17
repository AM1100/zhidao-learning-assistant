#!/usr/bin/env python3
"""Prepare local dependencies and start the server with the same interpreter."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更高版本。", file=sys.stderr)
        return 1
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    try:
        if not python.exists():
            if uv:
                subprocess.run([uv, "venv", "--python", sys.executable, str(ROOT / ".venv")], check=True)
            else:
                subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
        # Check actual imports, including Playwright's native dependencies.
        check = subprocess.run(
            [str(python), "-c", "import cryptography; from playwright.sync_api import sync_playwright"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if check.returncode:
            print("正在安装 requirements.txt 中的运行依赖……", flush=True)
            installer = ([uv, "pip", "install", "--python", str(python)] if uv
                         else [str(python), "-m", "pip", "install"])
            subprocess.run(installer + ["-r", str(ROOT / "requirements.txt")], check=True)
            subprocess.run([str(python), "-c", "import cryptography; from playwright.sync_api import sync_playwright"], check=True)
    except (OSError, subprocess.CalledProcessError):
        print(
            "环境准备失败，请查看上方错误。Ubuntu/Debian 若提示缺少 ensurepip，"
            "请安装与 Python 版本对应的 venv 包（如 sudo apt install python3-venv），"
            "或安装 uv 后重新运行本脚本。网络安装失败时检查网络后重试。",
            file=sys.stderr,
        )
        return 1
    os.chdir(ROOT)
    os.execv(str(python), [str(python), str(ROOT / "server.py"), *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
