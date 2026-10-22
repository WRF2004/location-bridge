"""Double-click launcher: install into a private venv once, then start locally."""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
DATA = Path.home() / "Library/Application Support/LocationBridge"
ENV = DATA / "venv"


def main():
    if sys.platform != "darwin":
        raise SystemExit("此启动包面向 Mac。请在 Mac 上解压并启动。")
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA.chmod(0o700)
    import fcntl
    lock = (DATA / "install.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    if not (ENV / "bin/python").exists():
        print("首次安装：正在准备独立运行环境…", flush=True)
        venv.EnvBuilder(with_pip=True).create(ENV)
    python = str(ENV / "bin/python")
    requirements = ROOT / "requirements.txt"
    version = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = DATA / "installed-version"
    if not marker.exists() or marker.read_text() != version:
        print("首次安装：下载所需组件，请保持网络连接。后续启动会跳过此步骤。", flush=True)
        subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)], check=True)
        marker.write_text(version)
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    # Keep Mac awake while this foreground session is alive; no permanent setting changes.
    os.execv("/usr/bin/caffeinate", ["caffeinate", "-i", python, str(ROOT / "server.py")])


if __name__ == "__main__":
    main()
